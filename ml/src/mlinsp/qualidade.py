"""Modelo de qualidade do marco M7: dados, pre-processamento e treino em dois estagios.

Usado por `train_quality`, `calibrate`, `evaluate` e `export_onnx` (um modulo por etapa do
plano 04, secao 4). O contrato com o backend esta aqui e em nenhum outro lugar:
`letterbox` + `normalizar` e exatamente o que `app/backend/.../qualidade_onnx.py` repete em
numpy, e `<versao>.json` (gerado por `export_onnx`) carrega os mesmos numeros.

Decisoes, com a evidencia de cada uma:
- Entrada 224 letterbox, nao 480x360 nativa: no Lab 2 (K1) nenhuma metrica de qualidade
  melhorou mais de 0,02 na nativa, e 224 custa 1/3 da latencia.
- Decisao so por `legivel` (kappa 0,82) e cena (0,74). Os defeitos tem kappa 0,15 a 0,41:
  rotulo humano de defeito NAO entra em perda nenhuma. A cabeca de defeitos aprende so com
  degradacao sintetica e sai como sinal informativo, fora do p_ruim da politica.
- Estagio 1 (pre-treino sintetico): fotos do treino (20 e 21/05) que a heuristica do M5
  considerou nitidas, cada uma em versao limpa e degradada; alvo = defeitos aplicados.
  `legivel` e cena ficam sem alvo: a foto limpa nao e necessariamente legivel.
- Estagio 2 (ajuste fino): os 163 rotulos humanos do treino (cena + legivel), com lotes
  sinteticos intercalados para a cabeca de defeitos nao perder o significado. Parada pela
  macro-F1 de `legivel` na validacao (22/05).
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import f1_score

from mlinsp import degradacao, modelo
from mlinsp.dados import CENAS, DEFEITOS, carregar_consolidar, carregar_imagem, ler_manifesto
from mlinsp.particao import particionar

LADO = 224
MEDIA = modelo.MEDIA
DESVIO = modelo.DESVIO
# Cor do preenchimento do letterbox: a media do ImageNet, que vira ~0 depois de normalizar.
PREENCHIMENTO = (124, 116, 104)  # round(MEDIA * 255)
VERSAO = "qualidade@v1"  # padrao do export_onnx; rodada nova exige --versao nova
# Nome do defeito no ml (esquema do Lab 2) -> classe de qualidade do backend (plano 04, 1.1).
DEFEITO_PARA_CLASSE = {
    "reflexo": "glare",
    "fora_de_foco": "fora_de_foco",
    "tampa_suja": "tampa_suja",
    "enquadramento": "enquadramento_incorreto",
    "display_apagado": "display_apagado",
}
assert list(DEFEITO_PARA_CLASSE) == DEFEITOS


# ---------------------------------------------------------------- pre-processamento


def letterbox(rgb: np.ndarray, lado: int = LADO) -> np.ndarray:
    """RGB uint8 de qualquer tamanho -> RGB uint8 (lado, lado), proporcao mantida.

    O lado maior vai a `lado` (INTER_AREA) e as faixas sao preenchidas com PREENCHIMENTO.
    """
    h, w = rgb.shape[:2]
    f = lado / max(h, w)
    nh, nw = round(h * f), round(w * f)
    peq = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.empty((lado, lado, 3), np.uint8)
    out[:] = PREENCHIMENTO
    y0, x0 = (lado - nh) // 2, (lado - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = peq
    return out


def normalizar(lote_uint8: torch.Tensor) -> torch.Tensor:
    """(N, 3, H, W) uint8 -> float32 normalizado com MEDIA/DESVIO, no device do lote."""
    media = torch.tensor(MEDIA, device=lote_uint8.device)[:, None, None]
    desvio = torch.tensor(DESVIO, device=lote_uint8.device)[:, None, None]
    return (lote_uint8.float() / 255.0 - media) / desvio


def para_tensor(imagens: list[np.ndarray]) -> torch.Tensor:
    """Lista de RGB uint8 (LADO, LADO, 3) -> (N, 3, LADO, LADO) uint8."""
    return torch.from_numpy(np.stack(imagens)).permute(0, 3, 1, 2).contiguous()


# ---------------------------------------------------------------- dados


def rotulos_humanos(repo: Path, rotulos_csv: Path) -> pd.DataFrame:
    """Uma linha por foto rotulada: cena e legivel por maioria (empate -> NA), com a mesma
    regra do app aplicada pelo `rotulador/consolidar.py`, mais `legivel_algum` (algum
    rotulador leu a foto: e o criterio de falso positivo, o mesmo do M5)."""
    cons = carregar_consolidar(repo)
    r = pd.read_csv(rotulos_csv, dtype=str, keep_default_na=False)
    r = r[r["pulada"] != "1"]
    linhas = []
    for foto, g in r.groupby("nome_arquivo", sort=True):
        saida = {}
        empates = set()
        for c in cons.CLASSES:
            valor, empate = cons.maioria(list(g[c]))
            saida[c] = valor
            if empate:
                empates.add(c)
        cons.aplicar_regra(saida, empates)
        linhas.append({
            "arquivo": foto,
            "cena": saida["cena"] if saida["cena"] in CENAS else None,
            "legivel": _binario(saida["legivel"]),
            "legivel_algum": int((g["legivel"] == "1").any()),
            "n_rotuladores": len(g),
            # So para avaliacao informativa da cabeca de defeitos: nao entra em perda.
            **{f"humano_{d}": _binario(saida[d]) for d in DEFEITOS},
        })
    return pd.DataFrame(linhas).set_index("arquivo")


def _binario(valor: str) -> float:
    return float(valor) if valor in ("0", "1") else np.nan


def base_particionada(base: Path) -> pd.DataFrame:
    """Manifesto das 12.340 fotos com `split` (treino/validacao/teste) por lote."""
    part, _ = particionar(ler_manifesto(base))
    return part.set_index("arquivo")


def alvos_humanos(rot: pd.DataFrame) -> dict[str, np.ndarray]:
    """Cena e legivel do rotulo humano; defeitos sempre NaN (kappa baixo: nao treina)."""
    mapa = {c: i for i, c in enumerate(CENAS)}
    return {
        "cena": rot["cena"].map(mapa).fillna(-1).astype(int).to_numpy(),
        "defeitos": np.full((len(rot), len(DEFEITOS)), np.nan, np.float32),
        "legivel": rot["legivel"].to_numpy(np.float32),
    }


def carregar_lote(caminhos: list[str]) -> torch.Tensor:
    return para_tensor([letterbox(carregar_imagem(c)) for c in caminhos])


@dataclass
class Sintetico:
    imagens: torch.Tensor  # (N, 3, LADO, LADO) uint8
    defeitos: np.ndarray  # (N, 5) float32, 1 = degradacao aplicada
    fontes: list[str]


def conjunto_sintetico(part: pd.DataFrame, p_ruim: pd.Series, n_fontes: int, seed: int,
                       cache: Path | None = None, p_ruim_max: float = 0.05) -> Sintetico:
    """Fontes: fotos do TREINO com p_ruim da heuristica <= p_ruim_max (provaveis boas,
    plano 04, 3.3). Cada fonte vira 1 variante limpa (so recompressao JPEG) e 1 degradada
    (1 ou 2 defeitos). Degradacao em 480x360, letterbox depois. Cache em .npz."""
    elegiveis = sorted(
        a for a in part.index[part["split"] == "treino"] if p_ruim.get(a, 1.0) <= p_ruim_max
    )
    rng = np.random.default_rng(seed)
    fontes = sorted(rng.choice(elegiveis, size=min(n_fontes, len(elegiveis)), replace=False))
    chave = hashlib.sha256(
        json.dumps([fontes, seed, Path(degradacao.__file__).read_text()]).encode()
    ).hexdigest()[:12]
    if cache is not None:
        arq = Path(cache) / f"sintetico_{chave}.npz"
        if arq.exists():
            with np.load(arq) as z:
                return Sintetico(torch.from_numpy(z["imagens"]), z["defeitos"], list(z["fontes"]))
    imgs, alvos = [], []
    for i, a in enumerate(fontes):
        img = carregar_imagem(part.at[a, "caminho"])
        semente = int(hashlib.sha256(f"{a}:{seed}".encode()).hexdigest()[:8], 16)
        for variante, rotulo in degradacao.gerar_sintetico(img, semente, n_degradadas=1):
            imgs.append(letterbox(variante))
            alvos.append([rotulo[d] for d in DEFEITOS])
    sint = Sintetico(para_tensor(imgs), np.asarray(alvos, np.float32),
                     [a for a in fontes for _ in range(2)])
    if cache is not None:
        arq.parent.mkdir(parents=True, exist_ok=True)
        np.savez(arq, imagens=sint.imagens.numpy(), defeitos=sint.defeitos,
                 fontes=np.asarray(sint.fontes))
    return sint


# ---------------------------------------------------------------- treino


@dataclass
class Config:
    sementes: tuple[int, ...] = (0, 1, 2)
    n_fontes_sinteticas: int = 2500
    semente_sintetico: int = 20261016
    lote: int = 32
    # Estagio 1 (plano: AdamW, lr 3e-4, cosseno). Menos epocas que as 20 do plano: o
    # conjunto sintetico tem 5.000 exemplos e a perda estabiliza antes (ver curva).
    epocas_1: int = 6
    lr_1: float = 3e-4
    # Estagio 2 (plano: lr 5e-5 com tudo destravado, early stopping). As cabecas de cena e
    # legivel nascem no estagio 2, por isso lr proprio maior. Paciencia 3 do plano parava
    # cedo demais: com 82 fotos a macro-F1 de validacao oscila +-0,03 entre epocas e duas
    # sementes pararam antes de aprender (rodada 20261016-212320). Por isso um minimo de
    # 10 epocas antes de parar, paciencia 5 e teto de 30.
    epocas_2: int = 30
    min_epocas_2: int = 10
    lr_2_extrator: float = 5e-5
    lr_2_cabecas: float = 1e-3
    paciencia: int = 5
    peso_sintetico_2: float = 0.5
    jitter: float = 0.10  # brilho e contraste, +-10% (plano 04, 4.2)
    p_flip: float = 0.5
    weight_decay: float = 1e-4
    extra: dict = field(default_factory=dict)


def _augmentar(x: torch.Tensor, cfg: Config, g: torch.Generator) -> torch.Tensor:
    """x float em [0, 1], (B, 3, H, W). Flip horizontal e jitter de brilho/contraste.
    Nunca blur nem brilho local: nao misturar augmentacao com o proprio rotulo."""
    b = x.shape[0]
    flip = torch.rand(b, generator=g) < cfg.p_flip
    x = torch.where(flip.to(x.device)[:, None, None, None], x.flip(-1), x)
    brilho = 1 + (torch.rand(b, generator=g) * 2 - 1) * cfg.jitter
    contraste = 1 + (torch.rand(b, generator=g) * 2 - 1) * cfg.jitter
    media = x.mean(dim=(1, 2, 3), keepdim=True)
    x = (x - media) * contraste.to(x.device)[:, None, None, None] + media
    return (x * brilho.to(x.device)[:, None, None, None]).clamp(0, 1)


def _entrada(lote_uint8: torch.Tensor, device, cfg: Config | None, g) -> torch.Tensor:
    x = lote_uint8.to(device).float() / 255.0
    if cfg is not None:
        x = _augmentar(x, cfg, g)
    media = torch.tensor(MEDIA, device=device)[:, None, None]
    desvio = torch.tensor(DESVIO, device=device)[:, None, None]
    return (x - media) / desvio


def _alvos_sinteticos(defeitos: np.ndarray) -> dict[str, torch.Tensor]:
    n = len(defeitos)
    return {
        "cena": torch.full((n,), -1, dtype=torch.long),
        "defeitos": torch.tensor(defeitos),
        "legivel": torch.full((n,), float("nan")),
    }


@torch.inference_mode()
def prever(m: modelo.ModeloTriagem, imagens: torch.Tensor, device, lote: int = 64):
    m.to(device).eval()
    partes: dict[str, list] = {}
    for i in range(0, len(imagens), lote):
        for k, v in m(_entrada(imagens[i : i + lote], device, None, None)).items():
            partes.setdefault(k, []).append(v.float().cpu().numpy())
    return {k: np.concatenate(v) for k, v in partes.items()}


def macro_f1_legivel(logit_legivel: np.ndarray, y: np.ndarray, limiar: float = 0.5) -> float:
    ok = ~np.isnan(y)
    p = 1 / (1 + np.exp(-logit_legivel[ok]))
    return float(f1_score(y[ok].astype(int), (p >= limiar).astype(int), average="macro",
                          labels=[0, 1], zero_division=0))


def treinar(seed: int, cfg: Config, sint: Sintetico, img_tr: torch.Tensor,
            alvos_tr: dict, img_va: torch.Tensor, y_va_legivel: np.ndarray, device,
            pretreinado: bool = True) -> tuple:
    """Os dois estagios para uma semente. Devolve (modelo no melhor epoch, historico)."""
    modelo.semear(seed)
    g = torch.Generator().manual_seed(seed)
    m = modelo.ModeloTriagem(pretreinado=pretreinado).to(device)
    hist: dict[str, list] = {"estagio1_perda": [], "estagio2_perda": [], "estagio2_val_f1": []}
    a_sint = _alvos_sinteticos(sint.defeitos)
    _, pw_sint, _ = modelo.pesos_desbalanceamento(
        {"cena": a_sint["cena"].numpy(), "defeitos": sint.defeitos,
         "legivel": a_sint["legivel"].numpy()})
    pw_sint = pw_sint.to(device)

    # Estagio 1: so a cabeca de defeitos tem alvo; o extrator inteiro treina.
    opt = torch.optim.AdamW(m.parameters(), lr=cfg.lr_1, weight_decay=cfg.weight_decay)
    passos = cfg.epocas_1 * -(-len(sint.imagens) // cfg.lote)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=passos)
    for _ in range(cfg.epocas_1):
        m.train()
        soma = 0.0
        for idx in torch.randperm(len(sint.imagens), generator=g).split(cfg.lote):
            opt.zero_grad()
            saida = m(_entrada(sint.imagens[idx], device, cfg, g))
            perda = modelo.perda_triagem(
                saida, {k: v[idx].to(device) for k, v in a_sint.items()}, pos_weight=pw_sint)
            perda.backward()
            opt.step()
            sched.step()
            soma += perda.item() * len(idx)
        hist["estagio1_perda"].append(soma / len(sint.imagens))

    # Estagio 2: rotulos humanos (cena + legivel) com lotes sinteticos intercalados.
    peso_cena, _, pw_leg = (t.to(device) for t in modelo.pesos_desbalanceamento(alvos_tr))
    a_tr = {k: torch.tensor(v) for k, v in alvos_tr.items()}
    opt = torch.optim.AdamW([
        {"params": m.extrator.parameters(), "lr": cfg.lr_2_extrator},
        {"params": m.cabecas.parameters(), "lr": cfg.lr_2_cabecas},
    ], weight_decay=cfg.weight_decay)
    melhor, melhor_f1, sem_melhora = copy.deepcopy(m.state_dict()), -1.0, 0
    for _ in range(cfg.epocas_2):
        m.train()
        soma, n = 0.0, 0
        ordem_sint = torch.randperm(len(sint.imagens), generator=g).split(cfg.lote)
        for j, idx in enumerate(torch.randperm(len(img_tr), generator=g).split(cfg.lote)):
            opt.zero_grad()
            saida = m(_entrada(img_tr[idx], device, cfg, g))
            perda = modelo.perda_triagem(
                saida, {k: v[idx].to(device) for k, v in a_tr.items()},
                peso_cena=peso_cena, pos_weight_leg=pw_leg)
            js = ordem_sint[j % len(ordem_sint)]
            s_sint = m(_entrada(sint.imagens[js], device, cfg, g))
            perda = perda + cfg.peso_sintetico_2 * modelo.perda_triagem(
                s_sint, {k: v[js].to(device) for k, v in a_sint.items()}, pos_weight=pw_sint)
            perda.backward()
            opt.step()
            soma += perda.item() * len(idx)
            n += len(idx)
        hist["estagio2_perda"].append(soma / n)
        f1 = macro_f1_legivel(prever(m, img_va, device)["legivel"], y_va_legivel)
        hist["estagio2_val_f1"].append(f1)
        if f1 > melhor_f1:
            melhor, melhor_f1, sem_melhora = copy.deepcopy(m.state_dict()), f1, 0
        else:
            sem_melhora += 1
            if sem_melhora >= cfg.paciencia and len(hist["estagio2_val_f1"]) >= cfg.min_epocas_2:
                break
    m.load_state_dict(melhor)
    hist["melhor_epoca_2"] = int(np.argmax(hist["estagio2_val_f1"])) + 1
    hist["melhor_val_f1"] = melhor_f1
    return m.eval(), hist


# ---------------------------------------------------------------- utilitarios


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def salvar_json(caminho: Path, obj) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=_json_padrao),
                       encoding="utf-8")


def _json_padrao(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Config):
        return asdict(o)
    raise TypeError(type(o))


def agora() -> str:
    return time.strftime("%Y%m%d-%H%M%S")

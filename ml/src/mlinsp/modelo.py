"""Triagem V1: MobileNetV3-Small pre-treinada (ImageNet) + cabecas de cena, defeitos e legivel.

| cabeca    | saidas | ativacao (so na inferencia) | perda              | por que                       |
| cena      | 4      | softmax                     | CrossEntropyLoss   | uma foto tem uma cena so      |
| defeitos  | 5      | sigmoide por defeito        | BCEWithLogitsLoss  | defeitos coexistem            |
| legivel   | 1      | sigmoide                    | BCEWithLogitsLoss  | binario, nao derivavel dos    |
|           |        |                             |                    | defeitos (ver notebook, B3)   |

As perdas recebem LOGITS. Nenhuma softmax/sigmoide entra antes delas.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

from mlinsp.dados import CENAS, DEFEITOS, carregar_imagem

ALTURA, LARGURA = 480, 360  # nativo do PDA, retrato 3:4
MEDIA = (0.485, 0.456, 0.406)
DESVIO = (0.229, 0.224, 0.225)
DIM = 576  # canais de saida do extrator da MobileNetV3-Small
TAREFAS = ("cena", "defeitos", "legivel")


def escolher_device() -> torch.device:
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


class Cabecas(nn.Module):
    """Cabecas lineares sobre o vetor de 576 do pool. `tarefas` permite o modelo separado
    (so cena, ou so qualidade) do K4."""

    def __init__(self, tarefas=TAREFAS, p_dropout: float = 0.2, dim: int = DIM):
        super().__init__()
        self.tarefas = tuple(tarefas)
        saidas = {"cena": len(CENAS), "defeitos": len(DEFEITOS), "legivel": 1}
        self.cabecas = nn.ModuleDict({
            t: nn.Sequential(nn.Dropout(p_dropout), nn.Linear(dim, saidas[t])) for t in self.tarefas
        })
        # Ultima camada pequena (como o classificador da propria MobileNetV3): logits ~ 0 no
        # inicio, perda inicial ~ ln(C) e ~ ln(2) qualquer que seja a escala das features.
        for c in self.cabecas.values():
            nn.init.normal_(c[1].weight, 0.0, 0.01)
            nn.init.zeros_(c[1].bias)

    def forward(self, z: torch.Tensor) -> dict[str, torch.Tensor]:
        out = {t: c(z) for t, c in self.cabecas.items()}
        if "legivel" in out:
            out["legivel"] = out["legivel"].squeeze(-1)
        return out

    def set_dropout(self, p: float) -> None:
        for c in self.cabecas.values():
            c[0].p = p


class ModeloTriagem(nn.Module):
    """Uma espinha dorsal, ate tres cabecas. forward devolve dict de LOGITS."""

    def __init__(self, pretreinado: bool = True, tarefas=TAREFAS, p_dropout: float = 0.2):
        super().__init__()
        pesos = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretreinado else None
        base = mobilenet_v3_small(weights=pesos)
        self.extrator = base.features
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.cabecas = Cabecas(tarefas, p_dropout, base.classifier[0].in_features)

    def features(self, x: torch.Tensor) -> torch.Tensor:
        return self.pool(self.extrator(x)).flatten(1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        return self.cabecas(self.features(x))

    def congelar_extrator(self, congelar: bool = True) -> None:
        for p in self.extrator.parameters():
            p.requires_grad = not congelar


# ---------------------------------------------------------------- entrada


def preprocessar(rgb: np.ndarray, resolucao: str = "nativa") -> torch.Tensor:
    """RGB uint8 (480, 360, 3) -> tensor normalizado (3, H, W).

    'nativa': 480x360 sem redimensionar. '224': letterbox, a foto vai a 224x168 mantendo
    3:4 e as faixas laterais sao preenchidas com a media do ImageNet (zero apos normalizar),
    sem distorcer a proporcao.
    """
    import cv2

    if resolucao == "224":
        h, w = rgb.shape[:2]
        f = 224 / max(h, w)
        nh, nw = round(h * f), round(w * f)
        rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        t = _normalizar(rgb)
        out = torch.zeros(3, 224, 224)
        y0, x0 = (224 - nh) // 2, (224 - nw) // 2
        out[:, y0:y0 + nh, x0:x0 + nw] = t
        return out
    if resolucao != "nativa":
        raise ValueError(f"resolucao desconhecida: {resolucao}")
    if rgb.shape[:2] != (ALTURA, LARGURA):
        rgb = cv2.resize(rgb, (LARGURA, ALTURA), interpolation=cv2.INTER_AREA)
    return _normalizar(rgb)


def _normalizar(rgb: np.ndarray) -> torch.Tensor:
    t = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).float() / 255.0
    return (t - torch.tensor(MEDIA)[:, None, None]) / torch.tensor(DESVIO)[:, None, None]


@torch.inference_mode()
def extrair_features(modelo: ModeloTriagem, chaves: list[str], imagens, resolucao: str,
                     device: torch.device, cache: Path | None = None,
                     lote: int = 64) -> np.ndarray:
    """Vetores de 576 (extrator congelado + pool) por imagem, com cache em .npz por chave.

    `imagens` e uma funcao chave -> RGB uint8 (assim serve para foto em disco e para
    variante sintetica). So calcula o que nao esta no cache.
    """
    feito: dict[str, np.ndarray] = {}
    if cache is not None and Path(cache).exists():
        with np.load(cache) as z:
            feito = {k: z[k] for k in z.files}
    faltam = [k for k in dict.fromkeys(chaves) if k not in feito]
    if faltam:
        modelo.eval().to(device)
        for i in range(0, len(faltam), lote):
            bloco = faltam[i:i + lote]
            x = torch.stack([preprocessar(imagens(k), resolucao) for k in bloco]).to(device)
            z = modelo.features(x).float().cpu().numpy()
            feito.update(zip(bloco, z))
        if cache is not None:
            Path(cache).parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, **feito)
    return np.stack([feito[k] for k in chaves]) if chaves else np.zeros((0, DIM), np.float32)


def imagem_de_arquivo(caminho_por_arquivo: dict[str, str]):
    return lambda arquivo: carregar_imagem(caminho_por_arquivo[arquivo])


# ---------------------------------------------------------------- inspecao


def tabela_de_formas(modelo: ModeloTriagem, x: torch.Tensor) -> list[tuple[str, tuple]]:
    """Forma da saida de cada bloco do extrator, do pool e de cada cabeca."""
    linhas: list[tuple[str, tuple]] = [("entrada", tuple(x.shape))]
    alvos = [(f"extrator.{n}", m) for n, m in modelo.extrator.named_children()]
    alvos += [("pool", modelo.pool)]
    alvos += [(f"cabeca_{t}", c) for t, c in modelo.cabecas.cabecas.items()]

    def gancho(nome):
        def f(_m, _i, o):
            linhas.append((nome, tuple(o.shape)))
        return f

    ganchos = [m.register_forward_hook(gancho(n)) for n, m in alvos]
    try:
        with torch.no_grad():
            modelo.eval()(x)
    finally:
        for g in ganchos:
            g.remove()
    return linhas


def contar_parametros(modelo: nn.Module) -> tuple[int, int]:
    total = sum(p.numel() for p in modelo.parameters())
    treinaveis = sum(p.numel() for p in modelo.parameters() if p.requires_grad)
    return total, treinaveis


@torch.inference_mode()
def medir_latencia(fn, x: torch.Tensor, device: torch.device, n: int = 50,
                   aquecimento: int = 10) -> dict[str, float]:
    """p50/p95/media em ms por chamada de fn(x), sincronizando o MPS a cada chamada."""
    x = x.to(device)

    def sync():
        if device.type == "mps":
            torch.mps.synchronize()

    for _ in range(aquecimento):
        fn(x)
    sync()
    tempos = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(x)
        sync()
        tempos.append((time.perf_counter() - t0) * 1000)
    a = np.array(tempos)
    return {"p50_ms": float(np.percentile(a, 50)), "p95_ms": float(np.percentile(a, 95)),
            "media_ms": float(a.mean())}


# ---------------------------------------------------------------- perda e treino


def perda_triagem(saidas: dict[str, torch.Tensor], alvos: dict[str, torch.Tensor],
                  peso_cena: torch.Tensor | None = None, pos_weight: torch.Tensor | None = None,
                  pos_weight_leg: torch.Tensor | None = None,
                  lam: float = 1.0) -> torch.Tensor:
    """CE(cena) + lam * [BCE(defeitos) + BCE(legivel)], ignorando rotulo ausente.

    alvos: cena long com -1 para ausente; defeitos float (B,5) e legivel float (B,), com
    NaN para ausente (empate na maioria, por exemplo).
    """
    total = saidas[next(iter(saidas))].new_zeros(())
    if "cena" in saidas:
        y = alvos["cena"]
        if (y >= 0).any():
            total = total + F.cross_entropy(saidas["cena"], y, weight=peso_cena, ignore_index=-1)
    for t, pw in (("defeitos", pos_weight), ("legivel", pos_weight_leg)):
        if t not in saidas:
            continue
        y = alvos[t]
        ok = ~torch.isnan(y)
        if ok.any():
            perda = F.binary_cross_entropy_with_logits(
                saidas[t], torch.nan_to_num(y), pos_weight=pw, reduction="none")
            total = total + lam * (perda * ok).sum() / ok.sum()
    return total


def alvos_de_rotulos(df) -> dict[str, np.ndarray]:
    """DataFrame de rotulos consolidados -> arrays (cena -1 e NaN para ausente)."""
    mapa = {c: i for i, c in enumerate(CENAS)}
    cena = df["cena"].map(mapa).fillna(-1).astype(int).to_numpy()
    defe = df[DEFEITOS].astype("Float64").to_numpy(dtype=np.float32, na_value=np.nan)
    leg = df["legivel"].astype("Float64").to_numpy(dtype=np.float32, na_value=np.nan)
    return {"cena": cena, "defeitos": defe, "legivel": leg}


def pesos_desbalanceamento(alvos: dict[str, np.ndarray], teto: float = 20.0):
    """Peso de cena ~ 1/frequencia; pos_weight = neg/pos por defeito (com teto).
    Classe sem exemplo no treino recebe peso 0 (nao ha o que aprender dela)."""
    c = alvos["cena"][alvos["cena"] >= 0]
    cont = np.bincount(c, minlength=len(CENAS)).astype(np.float32)
    peso_cena = np.where(cont > 0, cont.sum() / np.maximum(cont, 1) / max((cont > 0).sum(), 1), 0)
    d = alvos["defeitos"]
    pos = np.nansum(d, 0)
    neg = np.sum(~np.isnan(d), 0) - pos
    pw = np.clip(np.where(pos > 0, neg / np.maximum(pos, 1), 1.0), 1.0, teto)
    leg = alvos["legivel"][~np.isnan(alvos["legivel"])]
    pw_leg = np.clip((leg == 0).sum() / max((leg == 1).sum(), 1), 0.05, teto) if len(leg) else 1.0
    return (torch.tensor(peso_cena, dtype=torch.float32), torch.tensor(pw, dtype=torch.float32),
            torch.tensor(float(pw_leg)))


def semear(seed: int) -> None:
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def treinar_cabecas(x: np.ndarray, alvos: dict[str, np.ndarray], seed: int,
                    tarefas=TAREFAS, epocas: int = 60, lr: float = 1e-3,
                    weight_decay: float = 1e-2, p_dropout: float = 0.2,
                    lote: int = 64) -> tuple[Cabecas, dict]:
    """Extracao de caracteristicas: treina so as cabecas sobre os vetores de 576 congelados.

    As features sao padronizadas com media/desvio do treino (devolvidos em `norm`).
    Hiperparametros fixos e conservadores (poucas centenas de fotos para 576 x 10 pesos);
    nada e escolhido olhando a validacao.
    """
    semear(seed)
    mu, sd = x.mean(0), x.std(0) + 1e-6
    xt = torch.tensor((x - mu) / sd, dtype=torch.float32)
    yt = {k: torch.tensor(v) for k, v in alvos.items()}
    peso_cena, pw, pw_leg = pesos_desbalanceamento(alvos)
    cab = Cabecas(tarefas, p_dropout)
    opt = torch.optim.AdamW(cab.parameters(), lr=lr, weight_decay=weight_decay)
    g = torch.Generator().manual_seed(seed)
    n = len(xt)
    for _ in range(epocas):
        cab.train()
        for idx in torch.randperm(n, generator=g).split(lote):
            opt.zero_grad()
            perda = perda_triagem(cab(xt[idx]), {k: v[idx] for k, v in yt.items()},
                                  peso_cena, pw, pw_leg)
            perda.backward()
            opt.step()
    return cab.eval(), {"mu": mu, "sd": sd}


@torch.inference_mode()
def prever_cabecas(cab: Cabecas, norm: dict, x: np.ndarray) -> dict[str, np.ndarray]:
    """Logits por tarefa (numpy)."""
    xt = torch.tensor((x - norm["mu"]) / norm["sd"], dtype=torch.float32)
    return {k: v.numpy() for k, v in cab.eval()(xt).items()}


def ajuste_fino(modelo: ModeloTriagem, imagens: torch.Tensor, alvos: dict[str, np.ndarray],
                device: torch.device, seed: int, epocas: int = 6, lr_extrator: float = 1e-4,
                lr_cabeca: float = 1e-3, lote: int = 32) -> list[float]:
    """Ajuste fino completo com taxas diferenciadas (secao 8 do guia).

    `imagens` e uint8 (N, 3, H, W) ja no tamanho de entrada; a normalizacao e feita aqui,
    lote a lote, para nao guardar N x 3 x 480 x 360 floats em memoria.
    """
    semear(seed)
    modelo.to(device).train()
    peso_cena, pw, pw_leg = (t.to(device) for t in pesos_desbalanceamento(alvos))
    opt = torch.optim.AdamW([
        {"params": modelo.extrator.parameters(), "lr": lr_extrator},
        {"params": modelo.cabecas.parameters(), "lr": lr_cabeca},
    ], weight_decay=1e-4)
    media = torch.tensor(MEDIA, device=device)[:, None, None]
    desvio = torch.tensor(DESVIO, device=device)[:, None, None]
    yt = {k: torch.tensor(v) for k, v in alvos.items()}
    g = torch.Generator().manual_seed(seed)
    historico = []
    for _ in range(epocas):
        soma, cont = 0.0, 0
        for idx in torch.randperm(len(imagens), generator=g).split(lote):
            x = (imagens[idx].to(device).float() / 255.0 - media) / desvio
            opt.zero_grad()
            perda = perda_triagem(modelo(x), {k: v[idx].to(device) for k, v in yt.items()},
                                  peso_cena, pw, pw_leg)
            perda.backward()
            opt.step()
            soma += perda.item() * len(idx)
            cont += len(idx)
        historico.append(soma / cont)
    return historico


@torch.inference_mode()
def prever_imagens(modelo: ModeloTriagem, imagens: torch.Tensor, device: torch.device,
                   lote: int = 64) -> dict[str, np.ndarray]:
    modelo.to(device).eval()
    media = torch.tensor(MEDIA, device=device)[:, None, None]
    desvio = torch.tensor(DESVIO, device=device)[:, None, None]
    partes: dict[str, list] = {}
    for i in range(0, len(imagens), lote):
        x = (imagens[i:i + lote].to(device).float() / 255.0 - media) / desvio
        for k, v in modelo(x).items():
            partes.setdefault(k, []).append(v.float().cpu().numpy())
    return {k: np.concatenate(v) for k, v in partes.items()}

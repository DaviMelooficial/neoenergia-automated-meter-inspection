"""Exportacao do modelo de qualidade para ONNX (M7, T-M7-08), com verificacao de paridade.

    uv run python -m mlinsp.export_onnx --rodada <timestamp> --base "../Base cliente"

Gera `artifacts/<versao>.onnx` e `artifacts/<versao>.json` (ex.: `qualidade@v1.onnx`; o
json e o contrato lido pelo `DetectorOnnx` do backend) e copia o json para
`reports/<rodada>/qualidade.json`. Cada versao tem o seu arquivo: exportar a proxima nao
sobrescreve a que o worker carrega (MODELO_QUALIDADE_PATH aponta a versao em uso).

- opset 17, eixo de lote dinamico, entrada `float32[N, 3, 224, 224]` ja normalizada;
  saidas = LOGITS de cena (4), defeitos (5) e legivel (1). Temperatura, sigmoide e
  softmax ficam com o backend, lidas do json.
- Paridade obrigatoria: 100 fotos do teste no torch (CPU) e no onnxruntime; diferenca
  maxima absoluta abaixo de 1e-4 (criterio exato em `paridade`), senao a exportacao falha
  e nada e gravado.

O .onnx NAO vai para o git: ml/artifacts/ e ignorado (binario de ~4 MB que muda a cada
treino, derivado de pesos ImageNet de terceiros e de fotos do cliente). O json registra o
sha256 do .onnx exportado, e a copia em reports/<rodada>/ versiona esse hash. O backend
recusa um .onnx cujo hash nao bate com o json, e um json cuja versao, rodada, limiar ou
sha256 diverge da politica registrada para aquela versao (o sha amarra tambem a semente).
Por isso uma rodada nova so exporta com `--versao` nova (a guarda olha os qualidade.json ja
versionados em reports/), e a politica nova dela entra no backend com o sha impresso aqui.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from scipy.special import expit, softmax

from mlinsp import modelo, qualidade
from mlinsp.dados import CENAS, DEFEITOS

TOLERANCIA = 1e-4
N_PARIDADE = 100


class _Saidas(torch.nn.Module):
    """Dict de logits -> tupla, na ordem declarada no json."""

    def __init__(self, m: modelo.ModeloTriagem):
        super().__init__()
        self.m = m

    def forward(self, x):
        out = self.m(x)
        return out["cena"], out["defeitos"], out["legivel"]


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def origem_do_codigo(repo: Path) -> dict:
    """Commit do HEAD e se o codigo de treino/exportacao/backend tinha mudanca nao
    commitada: um json exportado de arvore suja nao reproduz pelo commit que cita."""
    sujo = _git(repo, "status", "--porcelain", "--", "ml/src", "app/backend/src")
    return {"commit": _git(repo, "rev-parse", "HEAD"),
            "arvore_suja": None if sujo is None else bool(sujo)}


def publicar(temporario: Path, destino: Path) -> None:
    """Move o .onnx novo para `destino`. Se ja existe um arquivo diferente com esse nome
    (mesma versao, outra semente ou outro torch), recusa: sobrescrever trocaria o modelo que
    o worker carrega por um que a politica nao calibrou. Arquivo identico e idempotente."""
    if destino.exists():
        novo, atual = qualidade.sha256(temporario), qualidade.sha256(destino)
        if atual != novo:
            temporario.unlink()
            raise SystemExit(
                f"{destino.name} ja existe com outro sha256 ({atual[:12]} != {novo[:12]}). "
                "Exporte com --versao nova; reexportar a mesma versao so e aceito se o arquivo "
                "for identico."
            )
    temporario.replace(destino)


def versao_ja_usada(reports: Path, versao: str, rodada: str) -> str | None:
    """Rodada que ja exportou `versao`, se nao for esta. Cada versao do detector pertence
    a uma rodada so: o backend amarra versao, rodada e limiar a uma politica registrada."""
    for arq in sorted(Path(reports).glob("*/qualidade.json")):
        c = json.loads(arq.read_text(encoding="utf-8"))
        if c.get("versao") == versao and c.get("rodada") != rodada:
            return c.get("rodada")
    return None


def exportar(m: modelo.ModeloTriagem, destino: Path) -> None:
    m = m.cpu().eval()
    exemplo = torch.zeros(1, 3, qualidade.LADO, qualidade.LADO)
    torch.onnx.export(
        _Saidas(m), (exemplo,), str(destino), opset_version=17, dynamo=False,
        input_names=["entrada"], output_names=["cena", "defeitos", "legivel"],
        dynamic_axes={n: {0: "lote"} for n in ("entrada", "cena", "defeitos", "legivel")},
    )


def paridade(m: modelo.ModeloTriagem, onnx: Path, imagens: torch.Tensor) -> dict:
    """Diferenca maxima absoluta torch x onnxruntime por saida, em logit e em probabilidade.

    O criterio (< 1e-4) vale para os logits das saidas que decidem ou aparecem como
    probabilidade calibrada (legivel, cena) e para a probabilidade de todas. Os logits de
    defeitos chegam a |12| e o ruido de float32 do onnxruntime neles passa de 1e-4 mesmo
    contra o torch em float64 (medido na rodada 20261016-213656); na probabilidade, que e
    o que o backend grava, a diferenca fica abaixo de 3e-5. Os numeros brutos ficam no json.
    """
    x = qualidade.normalizar(imagens)
    with torch.inference_mode():
        ref = [r.numpy() for r in _Saidas(m.cpu().eval())(x)]
    sess = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
    out = sess.run(None, {"entrada": x.numpy()})
    nomes = ("cena", "defeitos", "legivel")
    prob = {"cena": lambda z: softmax(z, axis=-1), "defeitos": expit, "legivel": expit}
    logit = {n: float(np.abs(r - o).max()) for n, r, o in zip(nomes, ref, out, strict=True)}
    probab = {n: float(np.abs(prob[n](r) - prob[n](o)).max())
              for n, r, o in zip(nomes, ref, out, strict=True)}
    criterio = max(logit["legivel"], logit["cena"], *probab.values())
    return {"max_dif_logit": logit, "max_dif_probabilidade": probab, "criterio": criterio}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rodada", required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path(".."))
    ap.add_argument("--reports", type=Path, default=Path("reports"))
    ap.add_argument("--artefatos", type=Path, default=Path("artifacts"))
    ap.add_argument("--versao", default=qualidade.VERSAO,
                    help="nome do detector; rodada nova exige versao nova (qualidade@v2, ...)")
    args = ap.parse_args(argv)
    dir_rep, dir_art = args.reports / args.rodada, args.artefatos / "m7" / args.rodada
    outra = versao_ja_usada(args.reports, args.versao, args.rodada)
    if outra:
        raise SystemExit(
            f"{args.versao} ja foi exportada pela rodada {outra}. Use --versao com um nome novo "
            "e registre a politica calibrada dele em app/backend/.../politica.py "
            "(POLITICA_DO_DETECTOR)."
        )
    cal = json.loads((dir_rep / "calibracao.json").read_text())
    av = json.loads((dir_rep / "avaliacao.json").read_text())
    semente = cal["semente_escolhida"]
    m = modelo.ModeloTriagem(pretreinado=False)
    m.load_state_dict(torch.load(dir_art / f"modelo_s{semente}.pt", map_location="cpu"))

    part = qualidade.base_particionada(args.base)
    teste = sorted(part.index[part["split"] == "teste"])
    rng = np.random.default_rng(semente)
    amostra = sorted(rng.choice(teste, size=N_PARIDADE, replace=False))
    imagens = qualidade.carregar_lote(list(part.loc[amostra, "caminho"]))

    # Um arquivo por versao: exportar a proxima nao sobrescreve a que o worker usa.
    temporario = args.artefatos / f"{args.versao}.onnx.tmp"
    exportar(m, temporario)
    par = paridade(m, temporario, imagens)
    if not par["criterio"] < TOLERANCIA:
        temporario.unlink()
        raise SystemExit(f"paridade torch x onnxruntime falhou: {par} (tolerancia {TOLERANCIA})")
    destino = args.artefatos / f"{args.versao}.onnx"
    publicar(temporario, destino)

    contrato = {
        "versao": args.versao,
        "rodada": args.rodada,
        "semente": semente,
        **origem_do_codigo(args.repo),
        "onnx_sha256": qualidade.sha256(destino),
        "onnx_bytes": destino.stat().st_size,
        "entrada": {
            "nome": "entrada", "layout": "NCHW", "dtype": "float32", "cores": "RGB",
            "lado": qualidade.LADO, "letterbox": "lado maior a 224 com INTER_AREA, centralizado",
            "preenchimento_rgb": list(qualidade.PREENCHIMENTO),
            "escala": 1 / 255, "media": list(qualidade.MEDIA), "desvio": list(qualidade.DESVIO),
        },
        "saidas": {"cena": CENAS, "defeitos": DEFEITOS, "legivel": ["legivel"]},
        "defeito_para_classe": qualidade.DEFEITO_PARA_CLASSE,
        "temperatura_legivel": cal["temperatura_legivel"],
        "temperatura_cena": cal["temperatura_cena"],
        "limiar_rejeita": cal["limiar"],
        "limiar_aprova": None,
        "decisao": "p_ilegivel = 1 - sigmoid(legivel / temperatura_legivel); defeitos so "
                   "informativos (kappa humano baixo), fora da decisao",
        "paridade": {"fotos": N_PARIDADE, "tolerancia": TOLERANCIA, **par},
        "teste": {
            "legivel_macro_f1": av["modelo_por_semente"][str(semente)]["legivel"]["macro_f1"],
            "rejeicao": av["modelo_por_semente"][str(semente)]["rejeicao"],
            "criterio_entrada": av["criterio_entrada"],
        },
    }
    qualidade.salvar_json(args.artefatos / f"{args.versao}.json", contrato)
    qualidade.salvar_json(dir_rep / "qualidade.json", contrato)
    print(f"{destino.name} ({contrato['onnx_bytes'] / 1e6:.1f} MB, sha256 "
          f"{contrato['onnx_sha256'][:12]}), paridade {par} em {N_PARIDADE} fotos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

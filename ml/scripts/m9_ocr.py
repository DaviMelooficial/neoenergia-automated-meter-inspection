"""Spike de OCR do marco M9, ponta a ponta.

    cd ml
    uv run --group ocr python scripts/m9_ocr.py amostrar     # sorteia as ~150 fotos
    uv run --group ocr python scripts/m9_ocr.py pranchas --saida /tmp/pranchas
    # conferencia manual: preencher leitura_conferida e recorte em reports/ocr_gabarito.csv
    uv run --group ocr python scripts/m9_ocr.py conferir     # leitura vista x registrada
    uv run --group ocr python scripts/m9_ocr.py rodar        # 4 abordagens + metricas

O gabarito e o resultado por foto tem leituras reais de medidor e ficam fora do git
(.gitignore); so `reports/ocr_metricas.csv` (agregado) e versionado.

Base das fotos: --base ou M9_BASE (padrao: "Base cliente/" na raiz do repo).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from mlinsp import ocr
from mlinsp.dados import PADRAO_PASTA, carregar_consolidar
from mlinsp.heuristicas import features_v0_lote

RAIZ = Path(__file__).resolve().parents[2]
REPORTS = RAIZ / "ml" / "reports"
GABARITO = REPORTS / "ocr_gabarito.csv"
POR_FOTO = REPORTS / "ocr_resultados_por_foto.csv"
METRICAS = REPORTS / "ocr_metricas.csv"
ROTULOS = RAIZ / "lab-02" / "rotulos" / "rotulos.csv"
COLUNAS = ["id_amostra", "arquivo", "lote", "cena", "lap_var_centro", "posicao_lida",
           "leitura_conferida", "conferencia", "recorte", "obs"]


def _base(arg: str | None) -> Path:
    return Path(arg or os.environ.get("M9_BASE") or RAIZ / "Base cliente")


def _ler_csvs(base: Path) -> pd.DataFrame:
    """Uma linha por foto citada nos CSVs: medidores, posicao lida e nota."""
    linhas = []
    for pasta in sorted(p for p in base.iterdir() if p.is_dir() and PADRAO_PASTA.match(p.name)):
        for caminho in sorted(pasta.glob("*.csv")):
            with open(caminho, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    foto = (r.get("Foto do medidor") or "").strip()
                    if foto in ("", "NA"):
                        continue
                    linhas.append({
                        "arquivo": foto,
                        "caminho": str(pasta / foto),
                        "medidores": (r.get("Numero do medidor") or "").strip(),
                        "posicao_lida": (r.get("Posicao do medidor lida") or "").strip(),
                        "nota": (r.get("Nota de Leitura Atual") or "").strip(),
                    })
    df = pd.DataFrame(linhas)
    # Foto citada em mais de uma linha teria duas leituras: fica fora do spike.
    return df[~df["arquivo"].duplicated(keep=False)].set_index("arquivo")


def _rotulos_maioria() -> pd.DataFrame:
    """cena e legivel por maioria dos rotuladores do Lab 2 (empate -> '')."""
    maioria = carregar_consolidar(RAIZ).maioria
    r = pd.read_csv(ROTULOS, dtype=str, keep_default_na=False)
    r = r[r["pulada"] != "1"]
    saida = {}
    for arquivo, g in r.groupby("nome_arquivo"):
        saida[arquivo] = {"lote": g["lote"].iloc[0], "cena": maioria(list(g["cena"]))[0],
                          "legivel": maioria(list(g["legivel"]))[0], "n_rotuladores": len(g)}
    return pd.DataFrame.from_dict(saida, orient="index")


def amostrar(args) -> None:
    if GABARITO.exists() and not args.forcar:
        sys.exit(f"{GABARITO} ja existe (tem conferencia manual). Use --forcar para refazer.")
    base = _base(args.base)
    rot = _rotulos_maioria()
    csvs = _ler_csvs(base)
    pool = rot[(rot["legivel"] == "1") & rot["cena"].isin(["digital", "eletromecanico"])]
    pool = pool.join(csvs, how="inner")
    n_legiveis = len(pool)
    pool = pool[~pool["medidores"].str.contains("/") & pool["posicao_lida"].str.isdigit()]
    print(f"legiveis rotuladas: {n_legiveis}; com 1 medidor e leitura numerica: {len(pool)}")

    feats = features_v0_lote(list(pool.index), list(pool["caminho"]))
    pool["lap_var_centro"] = feats["lap_var_centro"].round(1)

    # Cota por lote proporcional ao tamanho do pool do lote (maiores restos levam a sobra).
    tam = pool.groupby("lote").size()
    bruto = tam / tam.sum() * min(args.n, len(pool))
    cota = np.floor(bruto).astype(int)
    for lote in (bruto - cota).sort_values(ascending=False).index[: min(args.n, len(pool)) - cota.sum()]:
        cota[lote] += 1
    # Dentro do lote, a heuristica V0 desempata a legibilidade: saem as menos nitidas.
    escolhidas = pd.concat([
        g.sort_values("lap_var_centro", ascending=False).head(cota[lote])
        for lote, g in pool.groupby("lote")
    ])
    escolhidas = escolhidas.sample(frac=1.0, random_state=args.semente)  # embaralha para a conferencia
    escolhidas["arquivo"] = escolhidas.index
    escolhidas["id_amostra"] = range(1, len(escolhidas) + 1)
    for c in ("leitura_conferida", "conferencia", "recorte", "obs"):
        escolhidas[c] = ""
    escolhidas[COLUNAS].to_csv(GABARITO, index=False)
    print(f"{len(escolhidas)} fotos -> {GABARITO}")
    print(pd.crosstab(escolhidas["lote"], escolhidas["cena"], margins=True))


def _caminho(base: Path, arquivo: str) -> Path:
    pasta = "_".join(arquivo.split("_")[:4])  # PSP_EXTRATLEITIMPL_<data>_<hora>
    return base / pasta / arquivo


def _rgb(caminho: Path) -> np.ndarray:
    img = cv2.imread(str(caminho), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"imagem invalida: {caminho}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _grade(rgb: np.ndarray, rotulo: str, passo: int = 40) -> np.ndarray:
    """Foto em 2x com grade a cada `passo` px (coordenadas da foto original)."""
    img = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), None, fx=2, fy=2,
                     interpolation=cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    for x in range(0, w // 2, passo):
        cv2.line(img, (2 * x, 0), (2 * x, h), (0, 255, 255), 1)
        cv2.putText(img, str(x), (2 * x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    for y in range(0, h // 2, passo):
        cv2.line(img, (0, 2 * y), (w, 2 * y), (0, 255, 255), 1)
        cv2.putText(img, str(y), (2, 2 * y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    cv2.putText(img, rotulo, (w - 90, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 255), 2)
    return img


def pranchas(args) -> None:
    """Imagens para a conferencia manual (fora do repo: tem fotos do cliente).

    grade:    foto em 2x com grade de coordenadas, para marcar a caixa do display.
    recortes: o recorte marcado, ampliado, para ler os digitos e validar a caixa.
    """
    base, saida = _base(args.base), Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    gab = pd.read_csv(GABARITO, dtype=str, keep_default_na=False)
    if args.etapa == "grade":
        for i in range(0, len(gab), args.por_prancha):
            bloco = gab.iloc[i:i + args.por_prancha]
            imgs = [_grade(_rgb(_caminho(base, r.arquivo)), f"#{r.id_amostra}") for r in bloco.itertuples()]
            cv2.imwrite(str(saida / f"grade_{i // args.por_prancha + 1:02d}.png"), np.hstack(imgs))
    else:
        linhas = []
        for r in gab.itertuples():
            caixa = ocr.ler_caixa(r.recorte)
            if caixa is None:
                continue
            rec = ocr.recortar_caixa(_rgb(_caminho(base, r.arquivo)), caixa)
            rec = cv2.resize(cv2.cvtColor(rec, cv2.COLOR_RGB2BGR), (480, int(480 * rec.shape[0] / rec.shape[1])),
                             interpolation=cv2.INTER_CUBIC)
            faixa = np.full((rec.shape[0], 90, 3), 255, np.uint8)
            cv2.putText(faixa, f"#{r.id_amostra}", (4, rec.shape[0] // 2 + 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            linhas.append(np.hstack([faixa, rec, np.full((rec.shape[0], 10, 3), 255, np.uint8)]))
        for i in range(0, len(linhas), args.por_prancha):
            pilha = []
            for lin in linhas[i:i + args.por_prancha]:
                pilha += [lin, np.full((8, lin.shape[1], 3), 255, np.uint8)]
            cv2.imwrite(str(saida / f"recortes_{i // args.por_prancha + 1:02d}.png"), np.vstack(pilha))
    print(f"pranchas em {saida}")


def conferir(args) -> None:
    """Compara a leitura vista na foto com a registrada pelo leiturista."""
    gab = pd.read_csv(GABARITO, dtype=str, keep_default_na=False)
    estados = []
    for r in gab.itertuples():
        vista = ocr.normalizar_leitura(r.leitura_conferida)
        if not vista:
            estados.append("ilegivel")
        elif vista == ocr.normalizar_leitura(r.posicao_lida):
            estados.append("confere")
        else:
            estados.append("diverge")
    gab["conferencia"] = estados
    gab.to_csv(GABARITO, index=False)
    print(pd.crosstab(gab["cena"], gab["conferencia"], margins=True))


def _cronometrar(fn, rgb: np.ndarray) -> tuple[str, float]:
    t0 = time.perf_counter()
    texto = fn(rgb)
    return texto, time.perf_counter() - t0


def rodar(args) -> None:
    import torch

    torch.set_num_threads(args.threads)
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # Tesseract: medir 1 nucleo por foto
    base = _base(args.base)
    gab = pd.read_csv(GABARITO, dtype=str, keep_default_na=False)
    gab = gab[gab["conferencia"].isin(["confere", "diverge"])]
    leitor = ocr.LeitorEasyOCR()

    linhas = []
    for r in gab.itertuples():
        rgb = _rgb(_caminho(base, r.arquivo))
        caixa = ocr.ler_caixa(r.recorte)
        abordagens = {
            "tesseract_centro": ocr.ler_tesseract_centro,
            "easyocr": leitor.ler_foto,
        }
        if caixa is not None:
            abordagens["recorte_tesseract"] = lambda img, c=caixa: ocr.ler_recorte_tesseract(img, c)
            abordagens["recorte_easyocr"] = lambda img, c=caixa: leitor.ler_recorte(img, c)
        for nome, fn in abordagens.items():
            texto, seg = _cronometrar(fn, rgb)
            for ref, valor in (("conferida", r.leitura_conferida), ("posicao_lida", r.posicao_lida)):
                linhas.append({"abordagem": nome, "gabarito_ref": ref, "id_amostra": r.id_amostra,
                               "arquivo": r.arquivo, "lote": r.lote, "cena": r.cena,
                               "gabarito": ocr.normalizar_leitura(valor), "texto_ocr": texto,
                               "segundos": seg, **ocr.comparar(texto, valor)})
        print(f"#{r.id_amostra} ok", flush=True)

    res = pd.DataFrame(linhas)
    res.to_csv(POR_FOTO, index=False)
    agregados = []
    for ref, g in res.groupby("gabarito_ref", sort=False):
        for por in ([], ["cena"]):
            t = ocr.resumir(g, por=por)
            t.insert(0, "gabarito_ref", ref)
            if not por:
                t.insert(2, "cena", "todas")
            agregados.append(t)
    tab = pd.concat(agregados, ignore_index=True)
    tab.to_csv(METRICAS, index=False, float_format="%.4f")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(tab[tab["gabarito_ref"] == "conferida"].round(3).to_string(index=False))
    print(f"por foto -> {POR_FOTO} (fora do git); agregado -> {METRICAS}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", help='pasta com os lotes (padrao: M9_BASE ou "Base cliente/")')
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("amostrar")
    a.add_argument("--n", type=int, default=150)
    a.add_argument("--semente", type=int, default=9)
    a.add_argument("--forcar", action="store_true")
    a.set_defaults(fn=amostrar)
    b = sub.add_parser("pranchas")
    b.add_argument("--saida", required=True)
    b.add_argument("--etapa", choices=["grade", "recortes"], default="grade")
    b.add_argument("--por-prancha", type=int, default=3)
    b.set_defaults(fn=pranchas)
    c = sub.add_parser("conferir")
    c.set_defaults(fn=conferir)
    d = sub.add_parser("rodar")
    d.add_argument("--threads", type=int, default=1)
    d.set_defaults(fn=rodar)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

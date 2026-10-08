"""Triagem V0: sinais classicos de qualidade, sem treino (OpenCV).

Os sinais sao os do plano 04 (secao 2) mais estatisticas de histograma. Aqui so se
calculam as features; o limiar (ou o classificador sobre elas) e escolhido fora, na
validacao.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

NOMES_V0 = [
    "lap_var", "lap_var_centro", "tenengrad", "brilho_media", "contraste", "saturacao_media",
    "frac_estourados", "frac_escuros", "entropia_hist", "bordas_centro", "p01", "p99",
]


def _centro(img: np.ndarray, frac: float = 0.5) -> np.ndarray:
    h, w = img.shape[:2]
    dh, dw = int(h * (1 - frac) / 2), int(w * (1 - frac) / 2)
    return img[dh:h - dh, dw:w - dw]


def features_v0(rgb: np.ndarray) -> dict[str, float]:
    """Sinais V0 de uma foto RGB uint8.

    lap_var: variancia do Laplaciano (nitidez; baixa = fora de foco).
    frac_estourados: V > 250 e S < 30 no HSV (reflexo). frac_escuros: V < 40.
    entropia_hist: entropia do histograma de cinza (tampa suja/nevoa achata o histograma).
    bordas_centro: densidade de bordas Canny no centro (enquadramento).
    """
    cinza = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    lap = cv2.Laplacian(cinza, cv2.CV_64F)
    gx = cv2.Sobel(cinza, cv2.CV_64F, 1, 0)
    gy = cv2.Sobel(cinza, cv2.CV_64F, 0, 1)
    hist = np.bincount(cinza.ravel(), minlength=256).astype(np.float64)
    p = hist / hist.sum()
    p = p[p > 0]
    p01, p99 = np.percentile(cinza, [1, 99])
    return {
        "lap_var": float(lap.var()),
        "lap_var_centro": float(_centro(lap).var()),
        "tenengrad": float(np.mean(gx * gx + gy * gy)),
        "brilho_media": float(cinza.mean()),
        "contraste": float(cinza.std()),
        "saturacao_media": float(s.mean()),
        "frac_estourados": float(((v > 250) & (s < 30)).mean()),
        "frac_escuros": float((v < 40).mean()),
        "entropia_hist": float(-(p * np.log2(p)).sum()),
        "bordas_centro": float(cv2.Canny(_centro(cinza), 100, 200).mean() / 255.0),
        "p01": float(p01),
        "p99": float(p99),
    }


def _uma(caminho: str) -> dict[str, float]:
    img = cv2.imread(str(caminho), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"imagem invalida: {caminho}")
    return features_v0(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


def features_v0_lote(arquivos: list[str], caminhos: list[str], cache: Path | None = None,
                     n_threads: int = 8) -> pd.DataFrame:
    """Features V0 de varias fotos, indexadas por `arquivo`, com cache em parquet.

    So calcula o que falta no cache; o cache e por arquivo, entao serve para qualquer
    subconjunto (rotuladas, fotos de origem do K2 etc.).
    """
    feito = pd.DataFrame(columns=NOMES_V0)
    if cache is not None and Path(cache).exists():
        feito = pd.read_parquet(cache)
    faltam = [(a, c) for a, c in zip(arquivos, caminhos) if a not in feito.index]
    if faltam:
        with ThreadPoolExecutor(n_threads) as ex:  # cv2 solta o GIL
            novos = list(ex.map(_uma, [c for _, c in faltam]))
        novo = pd.DataFrame(novos, index=[a for a, _ in faltam])[NOMES_V0]
        feito = novo if feito.empty else pd.concat([feito, novo])
        feito.index.name = "arquivo"
        if cache is not None:
            Path(cache).parent.mkdir(parents=True, exist_ok=True)
            feito.to_parquet(cache)
    return feito.loc[list(arquivos), NOMES_V0]


def transformar_v0(x: pd.DataFrame | np.ndarray) -> np.ndarray:
    """log1p nas features de escala larga (nitidez); o resto passa como esta."""
    x = np.asarray(x, dtype=np.float64).copy()
    for i, nome in enumerate(NOMES_V0):
        if nome in ("lap_var", "lap_var_centro", "tenengrad"):
            x[:, i] = np.log1p(x[:, i])
    return x

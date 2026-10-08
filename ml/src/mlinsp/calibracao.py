"""Calibracao: ECE, diagrama de confiabilidade e temperature scaling.

A temperatura e ajustada SO na validacao (minimiza a NLL) e depois aplicada, sem
reajuste, no teste. Temperatura nao muda o argmax nem a ordem de confianca de uma
saida binaria: muda so o quanto o numero 'promete'.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit, log_softmax, softmax


def ece(confianca: np.ndarray, acerto: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error com bins de largura igual em [0, 1].

    ECE = soma_b (n_b / n) * |acuracia_b - confianca_media_b|.
    """
    confianca = np.asarray(confianca, dtype=np.float64)
    acerto = np.asarray(acerto, dtype=np.float64)
    if len(confianca) == 0:
        return float("nan")
    bordas = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(confianca, bordas[1:-1], right=True), 0, n_bins - 1)
    total = 0.0
    for b in range(n_bins):
        sel = idx == b
        if sel.any():
            total += sel.mean() * abs(acerto[sel].mean() - confianca[sel].mean())
    return float(total)


def confianca_multiclasse(logits: np.ndarray, y: np.ndarray, T: float = 1.0):
    """(confianca do top-1, acerto do top-1) com softmax(z / T)."""
    p = softmax(logits / T, axis=1)
    return p.max(1), (p.argmax(1) == y).astype(float)


def confianca_binaria(logits: np.ndarray, y: np.ndarray, T: float = 1.0):
    """Saida binaria vista como 2 classes: confianca = max(p, 1-p)."""
    p = expit(logits / T)
    return np.maximum(p, 1 - p), ((p >= 0.5).astype(int) == y).astype(float)


def _nll_softmax(T: float, logits, y):
    return -log_softmax(logits / T, axis=1)[np.arange(len(y)), y].mean()


def _nll_sigmoide(T: float, logits, y):
    z = logits / T
    return np.mean(np.logaddexp(0, z) - y * z)


def ajustar_temperatura(logits: np.ndarray, y: np.ndarray, tipo: str = "softmax") -> float:
    """T > 0 que minimiza a NLL em (logits, y). Busca em log T no intervalo [0,05; 20]."""
    y = np.asarray(y).astype(int)
    if len(y) == 0:
        return 1.0
    fn = _nll_softmax if tipo == "softmax" else _nll_sigmoide
    r = minimize_scalar(lambda lt: fn(np.exp(lt), logits, y), bounds=(np.log(0.05), np.log(20)),
                        method="bounded")
    return float(np.exp(r.x))


def diagrama_confiabilidade(ax, confianca, acerto, n_bins: int = 10, titulo: str = "",
                            rotulo: str = "") -> None:
    """Acuracia por bin de confianca contra a diagonal ideal; anota o ECE."""
    confianca, acerto = np.asarray(confianca), np.asarray(acerto)
    bordas = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(confianca, bordas[1:-1], right=True), 0, n_bins - 1)
    centros, acc, cont = [], [], []
    for b in range(n_bins):
        sel = idx == b
        if sel.any():
            centros.append(confianca[sel].mean())
            acc.append(acerto[sel].mean())
            cont.append(sel.sum())
    ax.plot([0, 1], [0, 1], ls="--", color="gray", lw=1)
    ax.plot(centros, acc, marker="o",
            label=f"{rotulo} ECE={ece(confianca, acerto, n_bins):.3f}".strip())
    for c, a, n in zip(centros, acc, cont):
        ax.annotate(str(n), (c, a), textcoords="offset points", xytext=(0, 5), fontsize=7,
                    ha="center")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("confianca media do bin")
    ax.set_ylabel("acuracia do bin")
    ax.set_title(titulo)
    ax.legend(loc="upper left", fontsize=8)

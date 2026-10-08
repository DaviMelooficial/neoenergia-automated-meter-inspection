"""Predicao seletiva: curva cobertura x risco e a saida ACEITA / REJEITADA / DUVIDA.

Saida de decisao da triagem (sobre p = P(legivel), calibrada):
    confianca = max(p, 1 - p)
    confianca >= limiar e p >= 0,5  -> ACEITA     (foto comprova a leitura)
    confianca >= limiar e p <  0,5  -> REJEITADA  (ilegivel, defeito ou sem medidor)
    caso contrario                  -> DUVIDA     (fila humana)

cobertura = fracao decidida (ACEITA + REJEITADA); risco = taxa de erro entre as decididas.
O limiar sai da validacao; no teste so e aplicado.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import beta


def curva_cobertura_risco(confianca: np.ndarray, acerto: np.ndarray):
    """Ordena por confianca decrescente; o ponto k decide as k mais confiantes.

    Devolve (cobertura, risco, limiar), um ponto por limiar distinto (empates de
    confianca entram juntos, como entrariam com um limiar real).
    """
    confianca = np.asarray(confianca, dtype=np.float64)
    acerto = np.asarray(acerto, dtype=np.float64)
    n = len(confianca)
    if n == 0:
        return np.array([]), np.array([]), np.array([])
    ordem = np.argsort(-confianca, kind="stable")
    c, a = confianca[ordem], acerto[ordem]
    erros = np.cumsum(1 - a)
    k = np.arange(1, n + 1)
    ultimo = np.r_[c[1:] != c[:-1], True]  # fim de cada grupo de confianca igual
    return k[ultimo] / n, erros[ultimo] / k[ultimo], c[ultimo]


def escolher_limiar(confianca, acerto, risco_max: float) -> dict:
    """Maior cobertura cujo risco nao passa de risco_max (na validacao).

    Se nenhum ponto atende, devolve limiar = inf (nada e decidido, tudo vai a DUVIDA).
    """
    cob, risco, lim = curva_cobertura_risco(confianca, acerto)
    ok = np.where(risco <= risco_max)[0]
    if len(ok) == 0:
        return {"limiar": float("inf"), "cobertura": 0.0, "risco": float("nan")}
    i = ok[np.argmax(cob[ok])]
    return {"limiar": float(lim[i]), "cobertura": float(cob[i]), "risco": float(risco[i])}


def aplicar_limiar(confianca, acerto, limiar: float) -> dict:
    confianca, acerto = np.asarray(confianca), np.asarray(acerto)
    dec = confianca >= limiar
    n_dec = int(dec.sum())
    n_err = int((1 - acerto[dec]).sum()) if n_dec else 0
    return {"n": len(confianca), "decididas": n_dec,
            "cobertura": n_dec / max(len(confianca), 1),
            "erros": n_err, "risco": n_err / n_dec if n_dec else float("nan"),
            "risco_sup95": limite_superior(n_err, n_dec)}


def limite_superior(erros: int, n: int, conf: float = 0.95) -> float:
    """Limite superior unilateral de Clopper-Pearson para a taxa de erro.

    Com 0 erros em n, da ~3/n (regra dos tres): e o que um conjunto pequeno consegue provar.
    """
    if n == 0:
        return float("nan")
    if erros >= n:
        return 1.0
    return float(beta.ppf(conf, erros + 1, n - erros))


def decidir(p_legivel: np.ndarray, limiar: float) -> np.ndarray:
    p = np.asarray(p_legivel)
    conf = np.maximum(p, 1 - p)
    return np.where(conf >= limiar, np.where(p >= 0.5, "ACEITA", "REJEITADA"), "DUVIDA")

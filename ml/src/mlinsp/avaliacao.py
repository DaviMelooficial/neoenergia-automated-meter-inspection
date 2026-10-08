"""Metricas da triagem e baselines nao profundas (V0), com 'n/d' quando falta exemplo.

Metricas (as mesmas para V0 e V1, sempre na validacao 22/05 ou no teste 03/07):
- cena: macro-F1 sobre as cenas presentes no conjunto avaliado;
- defeitos: F1 por defeito (limiar 0,5) e AUROC por defeito; macro-F1 de defeitos = media
  dos defeitos avaliaveis;
- legivel: AUROC e F1 (positivo = legivel).
Um defeito so e avaliado com >= MIN_POS positivos no treino e no conjunto avaliado.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from mlinsp.dados import CENAS, DEFEITOS

MIN_POS = 3


def _avaliavel(y_tr: np.ndarray | None, y: np.ndarray) -> bool:
    ok = (y.sum() >= MIN_POS) and ((1 - y).sum() >= MIN_POS)
    if y_tr is not None:
        ok = ok and y_tr.sum() >= MIN_POS and (1 - y_tr).sum() >= MIN_POS
    return bool(ok)


def metricas(pred: dict[str, np.ndarray], alvos: dict[str, np.ndarray],
             alvos_treino: dict[str, np.ndarray] | None = None) -> dict[str, float]:
    """pred: logits (cena (N,4), defeitos (N,5), legivel (N,)) ou probabilidades com a
    chave prefixada 'p_'. Devolve dict plano; NaN = n/d."""
    out: dict[str, float] = {}
    if "cena" in pred:
        y = alvos["cena"]
        ok = y >= 0
        presentes = sorted(set(y[ok]))
        if len(presentes) >= 2:
            yhat = pred["cena"][ok].argmax(1)
            out["cena_macro_f1"] = f1_score(y[ok], yhat, labels=presentes, average="macro",
                                            zero_division=0)
            out["cena_acuracia"] = float((yhat == y[ok]).mean())
        else:
            out["cena_macro_f1"] = out["cena_acuracia"] = np.nan
    if "defeitos" in pred:
        f1s = []
        for j, d in enumerate(DEFEITOS):
            y = alvos["defeitos"][:, j]
            ok = ~np.isnan(y)
            ytr = None
            if alvos_treino is not None:
                t = alvos_treino["defeitos"][:, j]
                ytr = t[~np.isnan(t)]
            if _avaliavel(ytr, y[ok]):
                p = expit(pred["defeitos"][ok, j])
                out[f"f1_{d}"] = f1_score(y[ok], p >= 0.5, zero_division=0)
                out[f"auroc_{d}"] = roc_auc_score(y[ok], p)
                f1s.append(out[f"f1_{d}"])
            else:
                out[f"f1_{d}"] = out[f"auroc_{d}"] = np.nan
        out["defeitos_macro_f1"] = float(np.mean(f1s)) if f1s else np.nan
    if "legivel" in pred:
        y = alvos["legivel"]
        ok = ~np.isnan(y)
        if _avaliavel(None, y[ok]):
            p = expit(pred["legivel"][ok])
            out["legivel_auroc"] = roc_auc_score(y[ok], p)
            out["legivel_f1"] = f1_score(y[ok], p >= 0.5, zero_division=0)
        else:
            out["legivel_auroc"] = out["legivel_f1"] = np.nan
    return out


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def treinar_baseline(x_tr: np.ndarray, alvos_tr: dict[str, np.ndarray], tipo: str = "logistica",
                     seed: int = 0):
    """Baseline nao profunda sobre features V0: um classificador por tarefa.

    'logistica': StandardScaler + regressao logistica (class_weight balanced). E
    deterministica: as 3 sementes dao o mesmo numero (desvio 0, e isso e dito no notebook).
    'floresta': RandomForest (300 arvores, balanced), varia com a semente.
    Devolve uma funcao x -> dict de logits no formato de `metricas`.
    """
    def novo():
        if tipo == "logistica":
            return make_pipeline(StandardScaler(), LogisticRegression(
                max_iter=2000, class_weight="balanced", C=1.0))
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=2,
                                      class_weight="balanced", random_state=seed, n_jobs=-1)

    modelos = {}
    y = alvos_tr["cena"]
    ok = y >= 0
    if len(set(y[ok])) >= 2:
        modelos["cena"] = (novo().fit(x_tr[ok], y[ok]), ok)
    for j, d in enumerate(DEFEITOS + ["legivel"]):
        yy = alvos_tr["defeitos"][:, j] if d != "legivel" else alvos_tr["legivel"]
        ok = ~np.isnan(yy)
        if ok.sum() and len(set(yy[ok])) == 2:
            modelos[d] = novo().fit(x_tr[ok], yy[ok].astype(int))

    def prever(x: np.ndarray) -> dict[str, np.ndarray]:
        out = {}
        if "cena" in modelos:
            m = modelos["cena"][0]
            proba = np.full((len(x), len(CENAS)), 1e-6)
            proba[:, m.classes_] = m.predict_proba(x)
            out["cena"] = np.log(np.clip(proba, 1e-6, 1))
        else:
            out["cena"] = np.zeros((len(x), len(CENAS)))
        defe = np.full((len(x), len(DEFEITOS)), -10.0)  # sem modelo: defeito ausente
        for j, d in enumerate(DEFEITOS):
            if d in modelos:
                defe[:, j] = _logit(modelos[d].predict_proba(x)[:, 1])
        out["defeitos"] = defe
        out["legivel"] = (_logit(modelos["legivel"].predict_proba(x)[:, 1])
                          if "legivel" in modelos else np.zeros(len(x)))
        return out

    return prever


def resumir_sementes(linhas: list[dict], colunas: list[str]) -> pd.DataFrame:
    """Linhas (uma por modelo x semente) -> 'media +- desvio' por modelo."""
    df = pd.DataFrame(linhas)
    saida = []
    for modelo, g in df.groupby("modelo", sort=False):
        item = {"modelo": modelo, "sementes": len(g)}
        for c in colunas:
            v = g[c].astype(float)
            item[c] = "n/d" if v.isna().all() else f"{v.mean():.3f} ± {v.std(ddof=0):.3f}"
        saida.append(item)
    return pd.DataFrame(saida)

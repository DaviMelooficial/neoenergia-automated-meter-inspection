"""Avaliacao do modelo de qualidade no TESTE (03/07) contra a heuristica do M5 (T-M7-06).

    uv run python -m mlinsp.evaluate --rodada <timestamp> --base "../Base cliente" \
        --exportacao artifacts/heuristicas_exportacao.csv

Metricas por semente (media +- desvio) e da semente escolhida, sempre por classe:
- legivel: F1 de cada classe, macro-F1, PR-AUC (positivo = ilegivel), AUROC, matriz de
  confusao a 0,5 e acuracia com IC de Wilson;
- rejeicao no limiar calibrado: rejeitadas, falsos positivos (algum rotulador leu),
  precisao com IC de Wilson, e limite superior de 95% do erro (Clopper-Pearson);
- cena: F1 por classe e macro-F1;
- defeitos (informativo, kappa humano 0,15 a 0,41): F1 e AUROC contra a maioria humana.

V0 = heuristica@v1 (p_ruim gravado no banco, exportado pelo make relatorio-heuristicas):
mesmo conjunto rotulado, limiar de macro-F1 escolhido na validacao, e a politica@v1 (0,85)
para a rejeicao. Cobertura da rejeicao tambem na base inteira (11.419 leituras com foto).

Criterio de entrada (decisao do Davi, 2026-10-16), os tres juntos:
  E1 macro-F1 de legivel no teste > a da V0 no mesmo conjunto;
  E2 zero falso positivo no teste com o limiar calibrado na validacao;
  E3 cobertura de rejeicao na base inteira >= a da V0 (0,76%).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from scipy.special import expit, softmax
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    roc_auc_score,
)

from mlinsp import modelo, qualidade, seletiva
from mlinsp.dados import CENAS, DEFEITOS
from mlinsp.relatorio_heuristicas import ler_exportacao

LIMIAR_V1 = 0.85  # politica@v1 (app/backend/src/inspecao/inference/politica.py)
COBERTURA_V0_BASE = 0.0076


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    """IC de 95% de Wilson para a proporcao k/n (plano 04, 4.3)."""
    if n == 0:
        return None
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    meia = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(float(centro - meia), 4), round(float(centro + meia), 4)]


def metricas_legivel(p_ilegivel: np.ndarray, y_legivel: np.ndarray, limiar_classe: float) -> dict:
    """Binario legivel x ilegivel. Predicao ilegivel quando p_ilegivel >= limiar_classe."""
    ok = ~np.isnan(y_legivel)
    p, y = p_ilegivel[ok], (1 - y_legivel[ok]).astype(int)  # y = 1 -> ilegivel
    yhat = (p >= limiar_classe).astype(int)
    f1 = f1_score(y, yhat, labels=[0, 1], average=None, zero_division=0)
    acertos = int((yhat == y).sum())
    return {
        "n": int(ok.sum()),
        "f1_legivel": float(f1[0]),
        "f1_ilegivel": float(f1[1]),
        "macro_f1": float(f1.mean()),
        "pr_auc_ilegivel": float(average_precision_score(y, p)),
        "auroc": float(roc_auc_score(y, p)),
        "acuracia": acertos / len(y),
        "acuracia_ic95": wilson(acertos, len(y)),
        "matriz_confusao": {"linhas_real": ["legivel", "ilegivel"],
                            "colunas_previsto": ["legivel", "ilegivel"],
                            "valores": confusion_matrix(y, yhat, labels=[0, 1]).tolist()},
    }


def rejeicao(p: np.ndarray, legivel_algum: np.ndarray, limiar: float | None) -> dict:
    if limiar is None:
        return {"rejeitadas": 0, "cobertura": 0.0, "falsos_positivos": 0, "precisao": None,
                "precisao_ic95": None, "erro_sup95": None}
    rej = p >= limiar
    n, fp = int(rej.sum()), int((rej & (legivel_algum == 1)).sum())
    return {"rejeitadas": n, "cobertura": n / len(p), "falsos_positivos": fp,
            "precisao": (n - fp) / n if n else None, "precisao_ic95": wilson(n - fp, n),
            "erro_sup95": seletiva.limite_superior(fp, n) if n else None}


def cobertura_maxima_zero_fp(p: np.ndarray, legivel_algum: np.ndarray) -> float:
    """Oraculo informativo: maior fracao rejeitavel no conjunto sem nenhum falso positivo."""
    lidas = p[legivel_algum == 1]
    fronteira = lidas.max() if len(lidas) else -np.inf
    return float((p > fronteira).mean())


def curva_rejeicao(p: np.ndarray, legivel_algum: np.ndarray):
    ordem = np.argsort(-p, kind="stable")
    fp = np.cumsum(legivel_algum[ordem] == 1)
    k = np.arange(1, len(p) + 1)
    return k / len(p), fp / k


def melhor_limiar_macro_f1(p: np.ndarray, y_legivel: np.ndarray) -> float:
    ok = ~np.isnan(y_legivel)
    cand = np.unique(p[ok])
    return float(max(cand, key=lambda t: metricas_legivel(p, y_legivel, t)["macro_f1"]))


def metricas_cena(logits: np.ndarray, cena: pd.Series, t: float) -> dict:
    y = cena.map({c: i for i, c in enumerate(CENAS)})
    ok = y.notna().to_numpy()
    yv = y[ok].astype(int).to_numpy()
    yhat = softmax(logits[ok] / t, axis=1).argmax(1)
    presentes = sorted(set(yv))
    f1 = f1_score(yv, yhat, labels=presentes, average=None, zero_division=0)
    return {"n": int(ok.sum()), "macro_f1": float(f1.mean()),
            "f1_por_classe": {CENAS[c]: float(v) for c, v in zip(presentes, f1)},
            "suporte": {CENAS[c]: int((yv == c).sum()) for c in presentes}}


def metricas_defeitos(logits: np.ndarray, rot: pd.DataFrame) -> dict:
    out = {}
    for j, d in enumerate(DEFEITOS):
        y = rot[f"humano_{d}"].to_numpy()
        ok = ~np.isnan(y)
        yv = y[ok].astype(int)
        p = expit(logits[ok, j])
        if 0 < yv.sum() < len(yv):
            out[d] = {"positivos": int(yv.sum()), "n": len(yv),
                      "f1": float(f1_score(yv, p >= 0.5, zero_division=0)),
                      "auroc": float(roc_auc_score(yv, p))}
        else:
            out[d] = {"positivos": int(yv.sum()), "n": len(yv), "f1": None, "auroc": None}
    return out


def media_desvio(valores: list[float]) -> str:
    a = np.asarray(valores, dtype=float)
    return f"{a.mean():.3f} +- {a.std(ddof=1) if len(a) > 1 else 0.0:.3f}"


@torch.inference_mode()
def p_ilegivel_base(m, caminhos: list[str], t: float, device, lote: int = 128) -> np.ndarray:
    out = []
    for i in range(0, len(caminhos), lote):
        x = qualidade.carregar_lote(caminhos[i : i + lote])
        out.append(1 - expit(qualidade.prever(m, x, device)["legivel"] / t))
    return np.concatenate(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rodada", required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--exportacao", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path(".."))
    ap.add_argument("--rotulos", type=Path, default=Path("../lab-02/rotulos/rotulos.csv"))
    ap.add_argument("--reports", type=Path, default=Path("reports"))
    ap.add_argument("--artefatos", type=Path, default=Path("artifacts"))
    args = ap.parse_args(argv)
    dir_rep, dir_art = args.reports / args.rodada, args.artefatos / "m7" / args.rodada
    cal = json.loads((dir_rep / "calibracao.json").read_text())
    humanos = qualidade.rotulos_humanos(args.repo, args.rotulos)
    rot = {s: pd.read_csv(dir_art / f"rotulos_{s}.csv").set_index("arquivo")
           .join(humanos.filter(like="humano_"))
           for s in ("validacao", "teste")}
    exp = ler_exportacao(args.exportacao)

    # ---- modelo, por semente
    por_semente = {}
    for s_txt, c in cal["por_semente"].items():
        with np.load(dir_art / f"logits_s{s_txt}.npz") as z:
            lg = dict(z)
        t = c["temperatura_legivel"]
        p_te = 1 - expit(lg["teste_legivel"] / t)
        por_semente[int(s_txt)] = {
            "legivel": metricas_legivel(p_te, rot["teste"]["legivel"].to_numpy(), 0.5),
            "rejeicao": rejeicao(p_te, rot["teste"]["legivel_algum"].to_numpy(), c["limiar"]),
            "cobertura_max_zero_fp": cobertura_maxima_zero_fp(
                p_te, rot["teste"]["legivel_algum"].to_numpy()),
            "cena": metricas_cena(lg["teste_cena"], rot["teste"]["cena"], c["temperatura_cena"]),
            "defeitos_informativo": metricas_defeitos(lg["teste_defeitos"], rot["teste"]),
            "_p_teste": p_te,
        }
    escolhida = cal["semente_escolhida"]
    sel = por_semente[escolhida]

    # ---- V0 no mesmo conjunto (fotos rotuladas que estao no banco)
    v0 = {}
    for s in ("validacao", "teste"):
        r = rot[s].join(exp["p_ruim"], how="inner")
        v0[s] = r
    t_v0 = melhor_limiar_macro_f1(v0["validacao"]["p_ruim"].to_numpy(),
                                  v0["validacao"]["legivel"].to_numpy())
    te_v0 = v0["teste"]
    m_v0 = {
        "n_teste_no_banco": len(te_v0),
        "limiar_macro_f1_validacao": t_v0,
        "legivel": metricas_legivel(te_v0["p_ruim"].to_numpy(), te_v0["legivel"].to_numpy(), t_v0),
        "rejeicao_politica_v1": rejeicao(te_v0["p_ruim"].to_numpy(),
                                         te_v0["legivel_algum"].to_numpy(), LIMIAR_V1),
        "cobertura_max_zero_fp": cobertura_maxima_zero_fp(
            te_v0["p_ruim"].to_numpy(), te_v0["legivel_algum"].to_numpy()),
        "cobertura_base": float((exp["p_ruim"] >= LIMIAR_V1).mean()),
    }

    # ---- modelo escolhido na base inteira (mesmas 11.419 leituras da exportacao)
    device = modelo.escolher_device()
    m = modelo.ModeloTriagem(pretreinado=False)
    m.load_state_dict(torch.load(dir_art / f"modelo_s{escolhida}.pt", map_location="cpu"))
    part = qualidade.base_particionada(args.base)
    caminhos = list(part.loc[exp.index, "caminho"])
    t0 = time.perf_counter()
    p_base = p_ilegivel_base(m, caminhos, cal["temperatura_legivel"], device)
    seg_base = time.perf_counter() - t0
    limiar = cal["limiar"]
    rej_base = (p_base >= limiar) if limiar is not None else np.zeros(len(p_base), bool)
    lotes = exp["lote"].to_numpy()
    base = {
        "leituras": len(p_base),
        "rejeitadas": int(rej_base.sum()),
        "cobertura": float(rej_base.mean()),
        "por_lote": {lote: {"leituras": int((lotes == lote).sum()),
                            "rejeitadas": int(rej_base[lotes == lote].sum())}
                     for lote in sorted(set(lotes))},
        "concordancia_com_v0": {
            "ambos_rejeitam": int((rej_base & (exp["p_ruim"].to_numpy() >= LIMIAR_V1)).sum()),
            "so_modelo": int((rej_base & (exp["p_ruim"].to_numpy() < LIMIAR_V1)).sum()),
            "so_v0": int((~rej_base & (exp["p_ruim"].to_numpy() >= LIMIAR_V1)).sum()),
        },
        "segundos_inferencia_mps": round(seg_base, 1),
    }
    pd.DataFrame({"arquivo": exp.index, "lote": lotes, "p_ilegivel": p_base,
                  "p_ruim_v0": exp["p_ruim"].to_numpy()}).to_csv(
        dir_art / "p_ilegivel_base.csv", index=False)

    # ---- criterio de entrada
    e1 = sel["legivel"]["macro_f1"] > m_v0["legivel"]["macro_f1"]
    e2 = limiar is not None and sel["rejeicao"]["falsos_positivos"] == 0
    e3 = base["cobertura"] >= COBERTURA_V0_BASE
    entrada = {"E1_macro_f1_maior_que_v0": bool(e1), "E2_zero_fp_no_teste": bool(e2),
               "E3_cobertura_base_maior_ou_igual_v0": bool(e3),
               "entra": bool(e1 and e2 and e3)}

    # ---- figura: cobertura x risco da rejeicao no teste
    fig, ax = plt.subplots(figsize=(7, 4.2))
    te = rot["teste"]
    cob, risco = curva_rejeicao(sel["_p_teste"], te["legivel_algum"].to_numpy())
    ax.step(cob, risco, where="post", label=f"{qualidade.VERSAO} (semente {escolhida})")
    cob0, risco0 = curva_rejeicao(te_v0["p_ruim"].to_numpy(), te_v0["legivel_algum"].to_numpy())
    ax.step(cob0, risco0, where="post", label="heuristica@v1 (V0)")
    if limiar is not None:
        ax.axvline(sel["rejeicao"]["cobertura"], color="C0", ls=":", lw=1,
                   label="modelo no limiar calibrado")
    ax.axvline(m_v0["rejeicao_politica_v1"]["cobertura"], color="C1", ls=":", lw=1,
               label="V0 na politica@v1 (0,85)")
    ax.set_xlabel("fracao do teste auto-rejeitada")
    ax.set_ylabel("falso positivo entre as rejeitadas")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 0.7)
    ax.legend(fontsize=7)
    ax.set_title("Teste (03/07): cobertura x risco da rejeicao", fontsize=10)
    fig.tight_layout()
    fig.savefig(dir_rep / "cobertura_risco_teste.png", dpi=110)
    plt.close(fig)

    resumo_sementes = {
        k: media_desvio([por_semente[s][grupo][campo] for s in por_semente])
        for k, (grupo, campo) in {
            "legivel_macro_f1": ("legivel", "macro_f1"),
            "legivel_pr_auc_ilegivel": ("legivel", "pr_auc_ilegivel"),
            "legivel_auroc": ("legivel", "auroc"),
            "cena_macro_f1": ("cena", "macro_f1"),
            "rejeicao_cobertura_teste": ("rejeicao", "cobertura"),
        }.items()
    }
    resumo_sementes["rejeicao_falsos_positivos_teste"] = [
        por_semente[s]["rejeicao"]["falsos_positivos"] for s in sorted(por_semente)]
    for s in por_semente.values():
        s.pop("_p_teste")
    qualidade.salvar_json(dir_rep / "avaliacao.json", {
        "rodada": args.rodada, "semente_escolhida": escolhida, "limiar_rejeita": limiar,
        "modelo_por_semente": por_semente, "modelo_media_desvio": resumo_sementes,
        "v0": m_v0, "base_inteira": base, "criterio_entrada": entrada,
    })
    print(json.dumps({"entrada": entrada, "modelo": {
        "macro_f1": sel["legivel"]["macro_f1"], "rejeicao": sel["rejeicao"],
        "base": {k: base[k] for k in ("rejeitadas", "cobertura")}},
        "v0": {"macro_f1": m_v0["legivel"]["macro_f1"], "rejeicao": m_v0["rejeicao_politica_v1"]},
        "sementes": resumo_sementes}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

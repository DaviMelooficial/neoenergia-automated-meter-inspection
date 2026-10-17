"""Avaliacao do modelo de qualidade no TESTE (03/07) contra a heuristica do M5 (T-M7-06).

    uv run python -m mlinsp.evaluate --rodada <timestamp> --base "../Base cliente" \
        --exportacao artifacts/heuristicas_exportacao.csv

Metricas por semente (media +- desvio) e da semente escolhida, sempre por classe:
- legivel: F1 de cada classe, macro-F1, PR-AUC (positivo = ilegivel), AUROC, matriz de
  confusao a 0,5 e acuracia com IC de Wilson;
- rejeicao no limiar calibrado: rejeitadas, falsos positivos (algum rotulador leu),
  precisao com IC bilateral de 95% de Wilson, e limite superior unilateral de 95% do erro
  por Clopper-Pearson (com 0 erros em n: 1 - 0,05^(1/n));
- cena: F1 por classe e macro-F1;
- defeitos (informativo, kappa humano 0,15 a 0,41): F1 e AUROC contra a maioria humana.

Heuristica = heuristica@v1 (p_ruim gravado no banco, exportado pelo make
relatorio-heuristicas): mesmo conjunto rotulado, limiar de macro-F1 escolhido na validacao.
A rejeicao dela sai em dois protocolos: a politica@v1 em operacao (0,85, escolhida no M5 por
inspecao visual) e o MESMO protocolo do modelo (zero FP na validacao), que e a comparacao
justa. Cobertura tambem na base inteira (11.419 leituras com foto, inclui treino e
validacao) e so no lote de teste. Bootstrap pareado no teste para as diferencas.

Criterio de entrada (decisao do Davi, 2026-10-16), os tres juntos:
  E1 macro-F1 de legivel no teste > a da heuristica no mesmo conjunto;
  E2 zero falso positivo no teste com o limiar calibrado na validacao;
  E3 cobertura de rejeicao na base inteira >= a da heuristica na politica@v1 (0,76%).
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
from mlinsp.calibrate import limiar_zero_fp
from mlinsp.dados import CENAS, DEFEITOS
from mlinsp.relatorio_heuristicas import ler_exportacao

LIMIAR_V1 = 0.85  # politica@v1 (app/backend/src/inspecao/inference/politica.py)
COBERTURA_HEURISTICA_BASE = 0.0076
LOTE_TESTE = "030726"
N_BOOTSTRAP = 5000


def concordancia(rej_modelo: np.ndarray, rej_heuristica: np.ndarray) -> dict:
    return {"ambos_rejeitam": int((rej_modelo & rej_heuristica).sum()),
            "so_modelo": int((rej_modelo & ~rej_heuristica).sum()),
            "so_heuristica": int((~rej_modelo & rej_heuristica).sum())}


def bootstrap_pareado(p_modelo: dict, rot_teste: pd.DataFrame, p_heuristica: pd.Series,
                      limiar_heuristica: float, n: int = N_BOOTSTRAP, seed: int = 0) -> dict:
    """Reamostra as fotos do teste (as mesmas para os dois) e mede modelo - heuristica em
    macro-F1 (modelo a 0,5, heuristica no limiar de macro-F1 da validacao), AUROC e PR-AUC.
    Devolve, por semente, os quantis 2,5/50/97,5% e P(delta <= 0)."""
    comum = rot_teste.index.intersection(p_heuristica.index)
    y_leg = rot_teste.loc[comum, "legivel"].to_numpy()
    ok = ~np.isnan(y_leg)
    y = (1 - y_leg[ok]).astype(int)
    ph = p_heuristica.loc[comum].to_numpy()[ok]
    pos = rot_teste.index.get_indexer(comum)
    rng = np.random.default_rng(seed)
    amostras = [i for i in (rng.integers(0, len(y), len(y)) for _ in range(n))
                if 0 < y[i].sum() < len(i)]
    saida = {}
    for s, p in p_modelo.items():
        pm = p[pos][ok]
        deltas = {"macro_f1": [], "auroc": [], "pr_auc": []}
        for i in amostras:
            yy = y[i]
            deltas["macro_f1"].append(
                f1_score(yy, pm[i] >= 0.5, average="macro", labels=[0, 1], zero_division=0)
                - f1_score(yy, ph[i] >= limiar_heuristica, average="macro", labels=[0, 1],
                           zero_division=0))
            deltas["auroc"].append(roc_auc_score(yy, pm[i]) - roc_auc_score(yy, ph[i]))
            deltas["pr_auc"].append(
                average_precision_score(yy, pm[i]) - average_precision_score(yy, ph[i]))
        saida[s] = {k: {"q2.5_50_97.5": np.round(np.percentile(v, [2.5, 50, 97.5]), 3).tolist(),
                        "p_delta_menor_ou_igual_0": round(float(np.mean(np.array(v) <= 0)), 3)}
                    for k, v in deltas.items()}
    saida["reamostras"] = len(amostras)
    return saida


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
                "precisao_ic95_wilson": None, "erro_sup95_clopper_pearson_unilateral": None}
    rej = p >= limiar
    n, fp = int(rej.sum()), int((rej & (legivel_algum == 1)).sum())
    return {"rejeitadas": n, "cobertura": n / len(p), "falsos_positivos": fp,
            "precisao": (n - fp) / n if n else None,
            "precisao_ic95_wilson": wilson(n - fp, n),
            "erro_sup95_clopper_pearson_unilateral": seletiva.limite_superior(fp, n) if n else None}


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

    # ---- heuristica@v1 no mesmo conjunto (fotos rotuladas que estao no banco)
    heu = {s: rot[s].join(exp["p_ruim"], how="inner") for s in ("validacao", "teste")}
    t_heu = melhor_limiar_macro_f1(heu["validacao"]["p_ruim"].to_numpy(),
                                   heu["validacao"]["legivel"].to_numpy())
    te_heu = heu["teste"]
    p_heu, la_heu = te_heu["p_ruim"].to_numpy(), te_heu["legivel_algum"].to_numpy()
    # Mesmo protocolo do modelo: limiar de zero FP na validacao, arredondado para cima.
    lim_mesmo = limiar_zero_fp(heu["validacao"]["p_ruim"].to_numpy(),
                               heu["validacao"]["legivel_algum"].to_numpy())["limiar"]
    lotes = exp["lote"].to_numpy()
    no_teste = np.char.find(lotes.astype(str), LOTE_TESTE) >= 0
    p_ruim_base = exp["p_ruim"].to_numpy()

    def cobertura_base(rej: np.ndarray) -> dict:
        return {"rejeitadas": int(rej.sum()), "cobertura": float(rej.mean()),
                "cobertura_lote_teste": float(rej[no_teste].mean())}

    m_heu = {
        "n_teste_no_banco": len(te_heu),
        "limiar_macro_f1_validacao": t_heu,
        "legivel": metricas_legivel(p_heu, te_heu["legivel"].to_numpy(), t_heu),
        "rejeicao_politica_v1": {
            "limiar": LIMIAR_V1, **rejeicao(p_heu, la_heu, LIMIAR_V1),
            "base": cobertura_base(p_ruim_base >= LIMIAR_V1)},
        "rejeicao_mesmo_protocolo": {
            "limiar": lim_mesmo, **rejeicao(p_heu, la_heu, lim_mesmo),
            "base": cobertura_base(p_ruim_base >= lim_mesmo) if lim_mesmo is not None else None},
        "cobertura_max_zero_fp": cobertura_maxima_zero_fp(p_heu, la_heu),
    }

    # ---- cada semente na base inteira (mesmas 11.419 leituras da exportacao)
    device = modelo.escolher_device()
    part = qualidade.base_particionada(args.base)
    caminhos = list(part.loc[exp.index, "caminho"])
    p_base_sem, seg_base = {}, {}
    for s_txt, c in cal["por_semente"].items():
        m = modelo.ModeloTriagem(pretreinado=False)
        m.load_state_dict(torch.load(dir_art / f"modelo_s{s_txt}.pt", map_location="cpu"))
        t0 = time.perf_counter()
        p_base_sem[int(s_txt)] = p_ilegivel_base(m, caminhos, c["temperatura_legivel"], device)
        seg_base[int(s_txt)] = round(time.perf_counter() - t0, 1)
        del m
    limiar = cal["limiar"]
    p_base = p_base_sem[escolhida]

    def rejeita(p: np.ndarray, lim) -> np.ndarray:
        return (p >= lim) if lim is not None else np.zeros(len(p), bool)

    rej_base = rejeita(p_base, limiar)
    rej_heu = p_ruim_base >= LIMIAR_V1
    base = {
        "leituras": len(p_base),
        **cobertura_base(rej_base),
        "por_lote": {lote: {"leituras": int((lotes == lote).sum()),
                            "rejeitadas": int(rej_base[lotes == lote].sum())}
                     for lote in sorted(set(lotes))},
        "concordancia_com_heuristica_v1": concordancia(rej_base, rej_heu),
        "concordancia_com_heuristica_mesmo_protocolo": (
            concordancia(rej_base, p_ruim_base >= lim_mesmo) if lim_mesmo is not None else None),
        "segundos_inferencia_mps": seg_base[escolhida],
        "nota": "a base inteira inclui as fotos de treino e validacao; ver cobertura_lote_teste",
    }
    pd.DataFrame({"arquivo": exp.index, "lote": lotes, "p_ilegivel": p_base,
                  "p_ruim_heuristica": p_ruim_base}).to_csv(dir_art / "p_ilegivel_base.csv", index=False)

    # ---- criterio de entrada, por semente (a escolhida decide; as outras mostram a variancia)
    def criterio(s: int) -> dict:
        e1 = por_semente[s]["legivel"]["macro_f1"] > m_heu["legivel"]["macro_f1"]
        lim_s = cal["por_semente"][str(s)]["limiar"]
        e2 = lim_s is not None and por_semente[s]["rejeicao"]["falsos_positivos"] == 0
        cob = float(rejeita(p_base_sem[s], lim_s).mean())
        e3 = cob >= COBERTURA_HEURISTICA_BASE
        return {"E1_macro_f1_maior_que_heuristica": bool(e1), "E2_zero_fp_no_teste": bool(e2),
                "E3_cobertura_base_maior_ou_igual_heuristica": bool(e3),
                "cobertura_base": cob, "entra": bool(e1 and e2 and e3)}

    criterio_sementes = {s: criterio(s) for s in sorted(por_semente)}
    entrada = criterio_sementes[escolhida]

    # ---- bootstrap pareado no teste: modelo (cada semente) menos heuristica
    bootstrap = bootstrap_pareado(
        {s: por_semente[s]["_p_teste"] for s in por_semente},
        rot["teste"], te_heu["p_ruim"], t_heu)

    # ---- figura: cobertura x risco da rejeicao no teste
    fig, ax = plt.subplots(figsize=(7, 4.2))
    te = rot["teste"]
    cob, risco = curva_rejeicao(sel["_p_teste"], te["legivel_algum"].to_numpy())
    ax.step(cob, risco, where="post", label=f"{qualidade.VERSAO} (semente {escolhida})")
    cob0, risco0 = curva_rejeicao(p_heu, la_heu)
    ax.step(cob0, risco0, where="post", label="heuristica@v1")
    if limiar is not None:
        ax.axvline(sel["rejeicao"]["cobertura"], color="C0", ls=":", lw=1,
                   label="modelo no limiar calibrado")
    ax.axvline(m_heu["rejeicao_politica_v1"]["cobertura"], color="C1", ls=":", lw=1,
               label="heuristica na politica@v1 (0,85)")
    if lim_mesmo is not None:
        ax.axvline(m_heu["rejeicao_mesmo_protocolo"]["cobertura"], color="C1", ls="--", lw=1,
                   label=f"heuristica no mesmo protocolo ({lim_mesmo:.3f})")
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
        "heuristica": m_heu, "base_inteira": base, "criterio_entrada": entrada,
        "criterio_entrada_por_semente": criterio_sementes, "bootstrap_pareado": bootstrap,
    })
    print(json.dumps({"entrada": entrada, "modelo": {
        "macro_f1": sel["legivel"]["macro_f1"], "rejeicao": sel["rejeicao"],
        "base": {k: base[k] for k in ("rejeitadas", "cobertura")}},
        "heuristica": {"macro_f1": m_heu["legivel"]["macro_f1"],
                       "politica_v1": m_heu["rejeicao_politica_v1"],
                       "mesmo_protocolo": m_heu["rejeicao_mesmo_protocolo"]},
        "por_semente": criterio_sementes, "bootstrap": bootstrap,
        "sementes": resumo_sementes}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

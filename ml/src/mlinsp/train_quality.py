"""Treino do modelo de qualidade (M7, T-M7-04 e T-M7-05): dois estagios, varias sementes.

    uv run python -m mlinsp.train_quality --base "../Base cliente" \
        --exportacao artifacts/heuristicas_exportacao.csv

Cria a rodada `reports/<timestamp>/` (versionada: hiperparametros, curvas, tempos e a
prova de reprodutibilidade) e `artifacts/m7/<timestamp>/` (fora do git: pesos e logits de
validacao e teste de cada semente). As etapas seguintes recebem `--rodada <timestamp>`.

Reprodutibilidade (aceite do M7): a primeira semente e treinada duas vezes do zero; os
logits de validacao e teste das duas execucoes precisam ser iguais.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch

from mlinsp import modelo, qualidade
from mlinsp.relatorio_heuristicas import ler_exportacao


def dados_rotulados(args):
    part = qualidade.base_particionada(args.base)
    rot = qualidade.rotulos_humanos(args.repo, args.rotulos)
    rot = rot.join(part[["caminho", "lote", "split"]], how="inner")
    return part, rot


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--exportacao", type=Path, required=True,
                    help="CSV de make relatorio-heuristicas (p_ruim da heuristica@v1)")
    ap.add_argument("--repo", type=Path, default=Path(".."))
    ap.add_argument("--rotulos", type=Path, default=Path("../lab-02/rotulos/rotulos.csv"))
    ap.add_argument("--reports", type=Path, default=Path("reports"))
    ap.add_argument("--artefatos", type=Path, default=Path("artifacts"))
    ap.add_argument("--sementes", type=int, nargs="+", default=list(qualidade.Config.sementes))
    ap.add_argument("--n-fontes", type=int, default=qualidade.Config.n_fontes_sinteticas,
                    help="fontes sinteticas (menor so para teste de fumaca)")
    ap.add_argument("--epocas-1", type=int, default=qualidade.Config.epocas_1)
    args = ap.parse_args(argv)

    cfg = qualidade.Config(sementes=tuple(args.sementes), n_fontes_sinteticas=args.n_fontes,
                           epocas_1=args.epocas_1)
    rodada = qualidade.agora()
    dir_rep = args.reports / rodada
    dir_art = args.artefatos / "m7" / rodada
    dir_art.mkdir(parents=True, exist_ok=True)
    device = modelo.escolher_device()
    t0 = time.perf_counter()

    part, rot = dados_rotulados(args)
    p_ruim = ler_exportacao(args.exportacao)["p_ruim"]
    sint = qualidade.conjunto_sintetico(part, p_ruim, cfg.n_fontes_sinteticas,
                                        cfg.semente_sintetico, cache=args.artefatos / "m7")
    t_dados = time.perf_counter() - t0
    conj = {s: rot[rot["split"] == s] for s in ("treino", "validacao", "teste")}
    imgs = {s: qualidade.carregar_lote(list(d["caminho"])) for s, d in conj.items()}
    alvos_tr = qualidade.alvos_humanos(conj["treino"])
    y_va = conj["validacao"]["legivel"].to_numpy(np.float32)
    for s, d in conj.items():
        d.reset_index()[["arquivo", "lote", "cena", "legivel", "legivel_algum"]].to_csv(
            dir_art / f"rotulos_{s}.csv", index=False)

    execucoes = []
    for i, seed in enumerate(list(cfg.sementes) + [cfg.sementes[0]]):
        repeticao = i == len(cfg.sementes)
        t = time.perf_counter()
        m, hist = qualidade.treinar(seed, cfg, sint, imgs["treino"], alvos_tr,
                                    imgs["validacao"], y_va, device)
        dur = time.perf_counter() - t
        logits = {s: qualidade.prever(m, imgs[s], device) for s in ("validacao", "teste")}
        nome = f"s{seed}" + ("_repeticao" if repeticao else "")
        np.savez(dir_art / f"logits_{nome}.npz",
                 **{f"{s}_{k}": v for s, d in logits.items() for k, v in d.items()})
        if not repeticao:
            torch.save(m.state_dict(), dir_art / f"modelo_{nome}.pt")
        execucoes.append({"semente": seed, "repeticao": repeticao, "segundos": round(dur, 1),
                          **hist})
        print(f"semente {seed}{' (repeticao)' if repeticao else ''}: {dur:.0f} s, "
              f"melhor epoca {hist['melhor_epoca_2']}, val macro-F1 {hist['melhor_val_f1']:.3f}")
        del m
        if device.type == "mps":
            torch.mps.empty_cache()

    a = np.load(dir_art / f"logits_s{cfg.sementes[0]}.npz")
    b = np.load(dir_art / f"logits_s{cfg.sementes[0]}_repeticao.npz")
    dif = max(float(np.abs(a[k] - b[k]).max()) for k in a.files)
    reprodutivel = {
        "semente": cfg.sementes[0],
        "max_dif_logits": dif,
        "mesma_val_f1": execucoes[0]["melhor_val_f1"] == execucoes[-1]["melhor_val_f1"],
        "mesma_epoca": execucoes[0]["melhor_epoca_2"] == execucoes[-1]["melhor_epoca_2"],
    }

    fig, eixos = plt.subplots(1, 3, figsize=(13, 3.6))
    for e in execucoes:
        if e["repeticao"]:
            continue
        rot_s = f"semente {e['semente']}"
        eixos[0].plot(range(1, len(e["estagio1_perda"]) + 1), e["estagio1_perda"], "o-", label=rot_s)
        eixos[1].plot(range(1, len(e["estagio2_perda"]) + 1), e["estagio2_perda"], "o-", label=rot_s)
        eixos[2].plot(range(1, len(e["estagio2_val_f1"]) + 1), e["estagio2_val_f1"], "o-",
                      label=rot_s)
    for ax, t in zip(eixos, ("estagio 1: perda (sintetico)", "estagio 2: perda (treino)",
                             "estagio 2: macro-F1 legivel (validacao)")):
        ax.set_title(t, fontsize=9)
        ax.set_xlabel("epoca")
        ax.legend(fontsize=7)
    fig.tight_layout()
    dir_rep.mkdir(parents=True, exist_ok=True)
    fig.savefig(dir_rep / "curvas_treino.png", dpi=110)
    plt.close(fig)

    qualidade.salvar_json(dir_rep / "treino.json", {
        "rodada": rodada,
        "device": device.type,
        "torch": torch.__version__,
        "config": cfg,
        "dados": {
            "rotuladas": {s: len(d) for s, d in conj.items()},
            "legivel_por_conjunto": {
                s: d["legivel"].value_counts(dropna=False).rename(str).to_dict()
                for s, d in conj.items()},
            "sintetico": {"fontes": len(set(sint.fontes)), "exemplos": len(sint.imagens),
                          "positivos_por_defeito": dict(zip(
                              qualidade.DEFEITOS, sint.defeitos.sum(0).astype(int).tolist()))},
            "segundos_preparo": round(t_dados, 1),
        },
        "execucoes": execucoes,
        "reprodutibilidade": reprodutivel,
        "segundos_total": round(time.perf_counter() - t0, 1),
    })
    pd.DataFrame(execucoes).drop(columns=["estagio1_perda", "estagio2_perda",
                                          "estagio2_val_f1"]).to_csv(dir_rep / "execucoes.csv",
                                                                     index=False)
    print(f"rodada {rodada}: reprodutibilidade {reprodutivel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

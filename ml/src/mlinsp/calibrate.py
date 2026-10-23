"""Calibracao do modelo de qualidade (M7, T-M7-07): temperatura e limiar de rejeicao.

    uv run python -m mlinsp.calibrate --rodada <timestamp>

Tudo na VALIDACAO (22/05); o teste so recebe o que sai daqui.

- Temperatura de `legivel` (e de cena, informativa) por minima NLL (`calibracao.py`).
- `p_ilegivel = 1 - sigmoid(z / T)` e o score de decisao (`ilegivel` na saida do detector).
- Limiar de rejeicao por predicao seletiva do lado da rejeicao: a maior cobertura de
  rejeicao com ZERO falso positivo na validacao (precisao exigida >= 0,99 com 82 fotos e o
  mesmo que zero erro). Falso positivo = foto que algum rotulador leu (`legivel_algum`).
  O limiar e o MENOR p_ilegivel rejeitado corretamente acima do maior p_ilegivel de foto
  lida, arredondado para cima em 3 casas (folga contra ruido numerico entre backends).
- `limiar_aprova` continua None: aprovar exige ~600 auto-aprovadas rotuladas (plano 04, 5.1).

Calibra todas as sementes (o evaluate reporta media e desvio) e escolhe a de maior
macro-F1 de `legivel` na validacao para exportar.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit

from mlinsp import calibracao, qualidade


def limiar_zero_fp(p_ilegivel: np.ndarray, legivel_algum: np.ndarray) -> dict:
    lidas = p_ilegivel[legivel_algum == 1]
    fronteira = float(lidas.max()) if len(lidas) else -np.inf
    acima = np.sort(p_ilegivel[(legivel_algum == 0) & (p_ilegivel > fronteira)])
    if len(acima) == 0:
        return {"limiar": None, "rejeitadas_val": 0, "cobertura_val": 0.0,
                "maior_p_de_lida": fronteira}
    # Arredondado para cima em 3 casas: o limiar exato seria o p de uma foto da validacao,
    # e o ruido numerico entre MPS, CPU e onnxruntime (~1e-5) decidiria para que lado ela
    # cai (aconteceu na rodada 20261016-213656). Para cima e o lado conservador.
    # Pode deixar a validacao sem nenhuma rejeitada; continua valido (e mais conservador).
    limiar = float(np.ceil(float(acima[0]) * 1000) / 1000)
    rej = p_ilegivel >= limiar
    return {"limiar": limiar, "rejeitadas_val": int(rej.sum()),
            "cobertura_val": float(rej.mean()), "maior_p_de_lida": fronteira}


def calibrar_semente(logits: dict, rot_va: pd.DataFrame) -> dict:
    z = logits["validacao_legivel"]
    y = rot_va["legivel"].to_numpy()
    ok = ~np.isnan(y)
    t_leg = calibracao.ajustar_temperatura(z[ok], y[ok].astype(int), "sigmoide")
    cena_y = rot_va["cena"].map({c: i for i, c in enumerate(qualidade.CENAS)})
    ok_c = cena_y.notna().to_numpy()
    t_cena = calibracao.ajustar_temperatura(logits["validacao_cena"][ok_c],
                                            cena_y[ok_c].astype(int).to_numpy(), "softmax")
    p_il = 1 - expit(z / t_leg)
    conf_antes, acerto_antes = calibracao.confianca_binaria(z[ok], y[ok].astype(int), 1.0)
    conf_depois, acerto_depois = calibracao.confianca_binaria(z[ok], y[ok].astype(int), t_leg)
    return {
        "temperatura_legivel": t_leg,
        "temperatura_cena": t_cena,
        "temperatura_no_limite": calibracao.t_no_limite(t_leg),
        "ece_legivel_antes": calibracao.ece(conf_antes, acerto_antes),
        "ece_legivel_depois": calibracao.ece(conf_depois, acerto_depois),
        **limiar_zero_fp(p_il, rot_va["legivel_algum"].to_numpy()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rodada", required=True)
    ap.add_argument("--reports", type=Path, default=Path("reports"))
    ap.add_argument("--artefatos", type=Path, default=Path("artifacts"))
    args = ap.parse_args(argv)
    dir_rep, dir_art = args.reports / args.rodada, args.artefatos / "m7" / args.rodada
    treino = json.loads((dir_rep / "treino.json").read_text())
    rot_va = pd.read_csv(dir_art / "rotulos_validacao.csv")

    por_semente = {}
    for e in treino["execucoes"]:
        if e["repeticao"]:
            continue
        with np.load(dir_art / f"logits_s{e['semente']}.npz") as z:
            por_semente[e["semente"]] = {
                "val_macro_f1": e["melhor_val_f1"], **calibrar_semente(dict(z), rot_va)}
    escolhida = max(por_semente, key=lambda s: (por_semente[s]["val_macro_f1"], -s))
    saida = {"rodada": args.rodada, "semente_escolhida": escolhida,
             "criterio_escolha": "maior macro-F1 de legivel na validacao",
             "limiar_aprova": None, "por_semente": por_semente,
             **{k: por_semente[escolhida][k] for k in
                ("temperatura_legivel", "temperatura_cena", "limiar")}}
    qualidade.salvar_json(dir_rep / "calibracao.json", saida)
    print(json.dumps({k: saida[k] for k in ("semente_escolhida", "temperatura_legivel",
                                            "limiar")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

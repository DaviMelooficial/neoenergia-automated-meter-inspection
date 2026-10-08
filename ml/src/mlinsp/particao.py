"""Particao por lote (temporal), com prova numerica de ausencia de vazamento.

Treino 20/05 + 21/05, validacao 22/05, teste 03/07. A unidade de vazamento e maior que a
foto: o mesmo medidor pode reaparecer em outra extracao. Por isso, depois de atribuir o
conjunto pelo lote, as fotos de validacao cujo id_externo ou numero de medidor ja esta no
treino saem da validacao, e as de teste que ja estao em treino ou validacao saem do teste.
"""

from __future__ import annotations

from itertools import combinations

import pandas as pd

SPLIT_POR_LOTE = {"200526": "treino", "210526": "treino", "220526": "validacao", "030726": "teste"}
ORDEM = ["treino", "validacao", "teste"]


def _medidores(df: pd.DataFrame) -> set[str]:
    return {m for ms in df["medidores"] for m in ms}


def particionar(manifesto: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Atribui `split` pelo lote e remove o que vazaria de um conjunto anterior.

    Devolve (df, relatorio). Em `df`, `split` e None para as fotos removidas e
    `motivo_remocao` diz por que. `relatorio` tem, por conjunto, quantas fotos o lote
    trazia, quantas sairam e quantas ficaram.
    """
    df = manifesto.copy()
    df["split"] = df["lote"].map(SPLIT_POR_LOTE)
    df["motivo_remocao"] = None
    vistos_ids: set[str] = set()
    vistos_med: set[str] = set()
    linhas = []
    for nome in ORDEM:
        sel = df["split"] == nome
        n_lote = int(sel.sum())
        rep_id = sel & df["id_externo"].isin(vistos_ids)
        rep_med = sel & df["medidores"].map(lambda ms, vm=vistos_med: any(m in vm for m in ms))
        remover = rep_id | rep_med
        df.loc[rep_med, "motivo_remocao"] = "medidor em conjunto anterior"
        df.loc[rep_id, "motivo_remocao"] = "id_externo em conjunto anterior"
        df.loc[remover, "split"] = None
        ficam = df["split"] == nome
        vistos_ids |= set(df.loc[ficam, "id_externo"])
        vistos_med |= _medidores(df.loc[ficam])
        linhas.append({"conjunto": nome,
                       "lotes": "+".join(k for k, v in SPLIT_POR_LOTE.items() if v == nome),
                       "fotos_no_lote": n_lote, "removidas_vazamento": int(remover.sum()),
                       "fotos_finais": int(ficam.sum()),
                       "sem_medidor_no_csv": int((ficam & df["medidores"].map(len).eq(0)).sum())})
    return df, pd.DataFrame(linhas)


def provar_sem_vazamento(df: pd.DataFrame) -> pd.DataFrame:
    """Interseccao de id_externo e de numero de medidor entre cada par de conjuntos.

    Tudo tem que dar zero. Usa a coluna `split` (fotos removidas, com split None, nao
    entram). Medidores multiplos ("a/b") contam cada um separadamente.
    """
    grupos = {s: df[df["split"] == s] for s in ORDEM}
    linhas = []
    for a, b in combinations(ORDEM, 2):
        ids = set(grupos[a]["id_externo"]) & set(grupos[b]["id_externo"])
        meds = _medidores(grupos[a]) & _medidores(grupos[b])
        linhas.append({"par": f"{a} x {b}", "fotos_a": len(grupos[a]), "fotos_b": len(grupos[b]),
                       "id_externo_em_comum": len(ids), "medidores_em_comum": len(meds)})
    return pd.DataFrame(linhas)


def assert_sem_vazamento(df: pd.DataFrame) -> None:
    prova = provar_sem_vazamento(df)
    total = int(prova["id_externo_em_comum"].sum() + prova["medidores_em_comum"].sum())
    if total:
        raise AssertionError(f"vazamento entre conjuntos:\n{prova}")


def medidores_em_comum_por_lote(df: pd.DataFrame) -> pd.DataFrame:
    """Tabela da secao 11 do guia, mas contando so fotos que existem (nao linhas do CSV)."""
    lotes = sorted(df["lote"].unique())
    meds = {lote: _medidores(df[df["lote"] == lote]) for lote in lotes}
    return pd.DataFrame([{"par_de_lotes": f"{a} x {b}", "medidores_em_comum": len(meds[a] & meds[b])}
                         for a, b in combinations(lotes, 2)])

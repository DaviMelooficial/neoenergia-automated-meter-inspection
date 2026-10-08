import pandas as pd
import pytest

from mlinsp.particao import assert_sem_vazamento, particionar, provar_sem_vazamento


def _foto(lote, id_externo, medidores):
    return {"arquivo": f"{lote}_{id_externo}.jpg", "lote": lote, "id_externo": id_externo,
            "medidores": medidores}


def _manifesto():
    return pd.DataFrame([
        _foto("200526", "1", ["M1"]),
        _foto("210526", "2", ["M2", "M3"]),
        _foto("220526", "3", ["M3"]),        # medidor do treino: sai da validacao
        _foto("220526", "4", ["M4"]),
        _foto("220526", "5", []),            # orfa: so o id_externo conta
        _foto("030726", "6", ["M4"]),        # medidor da validacao: sai do teste
        _foto("030726", "1", ["M9"]),        # id_externo do treino: sai do teste
        _foto("030726", "7", ["M7"]),
    ])


def test_particao_por_lote_remove_o_que_vazaria():
    df, rel = particionar(_manifesto())
    split = dict(zip(df["arquivo"], df["split"]))
    assert split["200526_1.jpg"] == "treino" and split["210526_2.jpg"] == "treino"
    assert pd.isna(split["220526_3.jpg"])
    assert split["220526_4.jpg"] == "validacao" and split["220526_5.jpg"] == "validacao"
    assert pd.isna(split["030726_6.jpg"]) and pd.isna(split["030726_1.jpg"])
    assert split["030726_7.jpg"] == "teste"
    rel = rel.set_index("conjunto")
    assert rel.loc["validacao", "removidas_vazamento"] == 1
    assert rel.loc["teste", "removidas_vazamento"] == 2
    assert rel.loc["validacao", "sem_medidor_no_csv"] == 1


def test_prova_zero_vazamento_depois_da_particao():
    df, _ = particionar(_manifesto())
    prova = provar_sem_vazamento(df)
    assert prova["id_externo_em_comum"].sum() == 0
    assert prova["medidores_em_comum"].sum() == 0
    assert_sem_vazamento(df)


def test_prova_acusa_vazamento_em_particao_aleatoria():
    df = _manifesto().assign(split=["treino", "treino", "validacao", "validacao", "validacao",
                                    "teste", "teste", "teste"])
    with pytest.raises(AssertionError):
        assert_sem_vazamento(df)
    prova = provar_sem_vazamento(df).set_index("par")
    assert prova.loc["treino x validacao", "medidores_em_comum"] == 1
    assert prova.loc["treino x teste", "id_externo_em_comum"] == 1

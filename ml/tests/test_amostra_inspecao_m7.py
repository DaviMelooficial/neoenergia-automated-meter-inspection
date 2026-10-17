import json

import pandas as pd

from mlinsp.amostra_inspecao_m7 import gerar, preservar_inspecao, so_o_modelo_rejeita


def _base() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "arquivo": ["d.jpg", "a.jpg", "b.jpg", "c.jpg", "e.jpg"],
            "lote": ["L2", "L1", "L1", "L1", "L2"],
            "p_ilegivel": [0.99, 0.976, 0.9759, 0.99, 0.5],
            "p_ruim_heuristica": [0.1, 0.849, 0.1, 0.85, 0.9],
        }
    )


def test_so_entra_o_que_so_o_modelo_rejeita_com_limiar_inclusivo():
    a = so_o_modelo_rejeita(_base(), 0.976)
    # a: limiar do modelo inclusivo, abaixo do 0,85; b: abaixo do limiar do modelo;
    # c: a v1 tambem rejeita (0,85 inclusivo); e: o modelo nao rejeita
    assert list(a["arquivo"]) == ["a.jpg", "d.jpg"]
    assert list(a.columns) == [
        "arquivo",
        "lote",
        "p_ilegivel",
        "p_ruim_heuristica",
        "legivel_inspecao",
        "inspetor",
    ]
    assert (a["legivel_inspecao"] == "").all() and (a["inspetor"] == "").all()


def test_regerar_preserva_o_que_ja_foi_inspecionado():
    nova = so_o_modelo_rejeita(_base(), 0.976)
    anterior = pd.DataFrame(
        {
            "arquivo": ["a.jpg", "fora.jpg"],
            "legivel_inspecao": ["0", "1"],
            "inspetor": ["davi", "davi"],
        }
    )
    r = preservar_inspecao(nova, anterior).set_index("arquivo")
    assert (r.at["a.jpg", "legivel_inspecao"], r.at["a.jpg", "inspetor"]) == ("0", "davi")
    assert (r.at["d.jpg", "legivel_inspecao"], r.at["d.jpg", "inspetor"]) == ("", "")
    assert "fora.jpg" not in r.index


def test_gerar_le_o_limiar_do_contrato_e_grava_na_pasta_da_rodada(tmp_path):
    (tmp_path / "reports" / "r1").mkdir(parents=True)
    (tmp_path / "artifacts" / "m7" / "r1").mkdir(parents=True)
    (tmp_path / "reports" / "r1" / "qualidade.json").write_text(
        json.dumps({"limiar_rejeita": 0.98}), encoding="utf-8"
    )
    _base().to_csv(tmp_path / "artifacts" / "m7" / "r1" / "p_ilegivel_base.csv", index=False)
    destino, n = gerar("r1", raiz=tmp_path)
    assert n == 1 and destino == tmp_path / "reports" / "r1" / "inspecao_355.csv"
    gravada = pd.read_csv(destino, dtype=str, keep_default_na=False)
    assert list(gravada["arquivo"]) == ["d.jpg"]
    gravada.loc[0, ["legivel_inspecao", "inspetor"]] = ["1", "ana"]
    gravada.to_csv(destino, index=False)
    gerar("r1", raiz=tmp_path)
    regravada = pd.read_csv(destino, dtype=str, keep_default_na=False)
    assert regravada.loc[0, ["legivel_inspecao", "inspetor"]].tolist() == ["1", "ana"]

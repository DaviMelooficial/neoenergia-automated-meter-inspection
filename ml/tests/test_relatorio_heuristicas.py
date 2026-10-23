import json

import numpy as np
import pandas as pd

from mlinsp.heuristicas import NOMES_V0
from mlinsp.relatorio_heuristicas import (
    CLASSES,
    amostra_inspecao,
    evidencia,
    ler_exportacao,
    paridade,
    tabela_limiar,
)


def _exportacao(tmp_path, n=400):
    p = np.linspace(0, 1, n)
    linhas = []
    for i, v in enumerate(p):
        sinais = {n: float(i) for n in NOMES_V0}
        saida = {c: 0.0 for c in CLASSES} | {"fora_de_foco": float(v), "sinais": sinais}
        revisor = {5: "auditor@neoenergia.com.br", 6: "auditor.e2e@exemplo.com.br",
                   7: "demo@exemplo.com.br"}.get(i)
        linhas.append({"arquivo": f"f{i:03d}.jpg", "lote": "L", "status": "fila_humana",
                       "saida": json.dumps(saida), "latencia_ms": 3,
                       "decisao_humana": "aprovada" if revisor else None,
                       "email_revisor": revisor})
    caminho = tmp_path / "exp.csv"
    pd.DataFrame(linhas).to_csv(caminho, index=False)
    return ler_exportacao(caminho)


def test_p_ruim_e_o_maior_score_entre_as_classes(tmp_path):
    exp = _exportacao(tmp_path)
    assert np.allclose(exp.p_ruim, exp.fora_de_foco)
    assert set(exp.classe_max) == {"fora_de_foco"}  # empate em 0 fica com a 1a coluna


def test_falso_positivo_e_rotulo_legivel_acima_do_limiar(tmp_path):
    exp = _exportacao(tmp_path)
    rot = pd.DataFrame({"legivel_algum": [1, 0], "legivel_todos": [0, 0], "n_rotuladores": [2, 1],
                        "cenas": ["digital", "digital"]}, index=["f399.jpg", "f398.jpg"])
    ev = evidencia(exp, rot)
    assert set(ev.fonte) == {"rotulo_lab02", "fila_humana"}
    # Decisao de conta E2E/demo nao e auditoria: so a do auditor real entra.
    assert list(ev[ev.fonte == "fila_humana"].index) == ["f005.jpg"]
    tab = tabela_limiar(exp, ev).set_index("limiar")
    assert tab.loc[1.0, "falsos_positivos"] == 1  # f399 tem p_ruim 1.0 e alguem leu
    assert tab.loc[0.5, "auto_rejeitadas"] == (exp.p_ruim >= 0.5).sum()


def test_amostra_tem_100_piores_e_100_limitrofes_sem_repetir(tmp_path):
    exp = _exportacao(tmp_path)
    am = amostra_inspecao(exp, limiar=0.9)
    assert am.grupo.value_counts().to_dict() == {"pior": 100, "limitrofe": 100}
    assert am.index.is_unique
    assert am[am.grupo == "pior"].p_ruim.min() >= am[am.grupo == "limitrofe"].p_ruim.max()
    assert (am.decisao_v1 == np.where(am.p_ruim >= 0.9, "rejeitar", "humano")).all()


def test_paridade_compara_os_sinais_gravados_com_o_calculado(tmp_path):
    exp = _exportacao(tmp_path, n=4)
    feat = pd.DataFrame(list(exp.sinais), index=exp.index)
    assert paridade(feat, exp) == {"fotos": 4, "fotos_sem_sinais": 0, "max_dif_abs": 0.0,
                                   "max_dif_rel": 0.0, "fotos_com_diferenca": 0,
                                   "identicas": True}
    feat.loc["f002.jpg", "tenengrad"] += 1.0
    r = paridade(feat, exp)
    assert r["fotos_com_diferenca"] == 1 and r["max_dif_rel"] == 0.5 and not r["identicas"]


def test_paridade_nao_declara_identicas_com_saida_sem_sinais(tmp_path):
    exp = _exportacao(tmp_path, n=4)
    feat = pd.DataFrame(list(exp.sinais), index=exp.index)
    exp.at["f001.jpg", "sinais"] = {}
    r = paridade(feat, exp)
    assert r["fotos"] == 3 and r["fotos_sem_sinais"] == 1
    assert r["fotos_com_diferenca"] == 0 and not r["identicas"]


def test_paridade_trata_sinais_null_como_saida_sem_sinais(tmp_path):
    exp = _exportacao(tmp_path, n=3)
    caminho = tmp_path / "exp_null.csv"
    bruto = pd.read_csv(tmp_path / "exp.csv")
    saida = json.loads(bruto.at[0, "saida"])
    saida["sinais"] = None
    bruto.at[0, "saida"] = json.dumps(saida)
    bruto.to_csv(caminho, index=False)
    exp = ler_exportacao(caminho)
    feat = pd.DataFrame([{n: float(i) for n in NOMES_V0} for i in range(3)], index=exp.index)
    r = paridade(feat, exp)
    assert r["fotos_sem_sinais"] == 1 and r["fotos"] == 2 and not r["identicas"]


def test_paridade_conta_nan_no_calculado_como_diferenca(tmp_path):
    exp = _exportacao(tmp_path, n=3)
    feat = pd.DataFrame(list(exp.sinais), index=exp.index)
    feat.loc["f001.jpg", "lap_var"] = np.nan
    r = paridade(feat, exp)
    assert r["fotos_com_diferenca"] == 1 and not r["identicas"]

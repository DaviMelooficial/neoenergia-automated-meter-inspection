import importlib.util
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score

from mlinsp.calibracao import ajustar_temperatura, confianca_multiclasse, ece
from mlinsp.dados import kappa_por_classe
from mlinsp.seletiva import curva_cobertura_risco, escolher_limiar, limite_superior

RAIZ = Path(__file__).resolve().parents[2]


def _consolidar():
    spec = importlib.util.spec_from_file_location("consolidar", RAIZ / "rotulador" / "consolidar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_kappa_do_rotulador_bate_com_sklearn(seed):
    rng = np.random.default_rng(seed)
    a = rng.choice(["digital", "eletromecanico", "ocorrencia"], 60).tolist()
    b = [x if rng.random() < 0.7 else str(rng.choice(["digital", "ocorrencia"])) for x in a]
    k = _consolidar().kappa_cohen(a, b)
    assert k == pytest.approx(cohen_kappa_score(a, b), abs=1e-12)


def test_kappa_por_classe_media_ponderada():
    import pandas as pd

    linhas = []
    for i in range(10):
        for r in ("ana", "bia"):
            v = "1" if (i < 5 or (r == "bia" and i == 9)) else "0"
            linhas.append({"nome_arquivo": f"f{i}", "rotulador": r, "cena": "digital", "reflexo": v})
    tab = kappa_por_classe(pd.DataFrame(linhas), _consolidar().kappa_cohen, ["cena", "reflexo"])
    tab = tab.set_index("classe")
    a = ["1"] * 5 + ["0"] * 5
    b = ["1"] * 5 + ["0"] * 4 + ["1"]
    assert tab.loc["reflexo", "kappa"] == pytest.approx(cohen_kappa_score(a, b))
    assert pd.isna(tab.loc["cena", "kappa"])  # uma categoria so: kappa indefinido
    assert tab.loc["cena", "alerta"] == "n/d"


def test_ece_casos_conhecidos():
    assert ece(np.full(100, 0.8), np.r_[np.ones(80), np.zeros(20)]) == pytest.approx(0.0)
    assert ece(np.full(10, 0.9), np.zeros(10)) == pytest.approx(0.9)
    conf = np.r_[np.full(50, 0.95), np.full(50, 0.55)]
    acerto = np.r_[np.ones(45), np.zeros(5), np.ones(25), np.zeros(25)]
    assert ece(conf, acerto) == pytest.approx(0.5 * 0.05 + 0.5 * 0.05)


def test_temperatura_corrige_superconfianca():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, 2000)
    logits = rng.normal(0, 1, (2000, 3))
    logits[np.arange(2000), y] += 1.0
    quente = logits * 5  # mesma ordem, confianca exagerada
    T = ajustar_temperatura(quente, y, "softmax")
    assert T == pytest.approx(5, rel=0.15)
    antes = ece(*confianca_multiclasse(quente, y, 1.0))
    depois = ece(*confianca_multiclasse(quente, y, T))
    assert depois < antes / 3


def test_curva_cobertura_risco_e_limiar():
    conf = np.array([0.99, 0.95, 0.9, 0.8, 0.7, 0.6])
    acerto = np.array([1, 1, 1, 0, 1, 0])
    cob, risco, _ = curva_cobertura_risco(conf, acerto)
    assert cob.tolist() == pytest.approx([1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6, 1])
    assert risco[2] == 0 and risco[3] == pytest.approx(0.25)
    esc = escolher_limiar(conf, acerto, 0.0)
    assert esc["limiar"] == 0.9 and esc["cobertura"] == pytest.approx(0.5)
    assert escolher_limiar(conf, 1 - acerto, 0.0)["cobertura"] == 0.0


def test_limite_superior_regra_dos_tres():
    assert limite_superior(0, 150) == pytest.approx(3 / 150, rel=0.05)


def test_temperatura_no_limite_quando_saida_nao_discrimina():
    from mlinsp.calibracao import t_no_limite

    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 400)
    logits = rng.normal(0, 5, 400)  # ruido puro, confiante: so achatar ajuda
    T = ajustar_temperatura(logits, y, "sigmoide")
    assert t_no_limite(T)
    assert not t_no_limite(1.0)

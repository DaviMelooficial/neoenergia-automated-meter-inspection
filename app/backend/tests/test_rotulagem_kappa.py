"""Kappa de Cohen e consolidacao por maioria, sem banco.

Os valores esperados sao calculados a mao nos comentarios. O ultimo teste e a
ancora: os 500 rotulos do Lab 2 precisam dar o mesmo kappa de
`lab-02/resultados/resumo.json`, que foi medido por `rotulador/consolidar.py`.
"""

import csv
import json
from collections import defaultdict

import pytest

from inspecao.config import RAIZ_REPO
from inspecao.enums import RodadaRotulagem
from inspecao.rotulagem import relatorio
from inspecao.rotulagem.consolidacao import (
    CLASSES,
    DEFEITOS,
    KappaPar,
    consolidar,
    kappa_cohen,
    kappa_medio,
    kappa_por_par,
    maioria,
)


def test_kappa_binario_com_valor_conhecido():
    # po = 3/4; p_a(1) = 1/2, p_b(1) = 1/4; pe = 1/2*1/4 + 1/2*3/4 = 1/2
    # kappa = (3/4 - 1/2) / (1 - 1/2) = 0,5
    assert kappa_cohen([1, 1, 0, 0], [1, 0, 0, 0]) == pytest.approx(0.5)


def test_kappa_multiclasse_com_valor_conhecido():
    # po = 3/4; pe = (2*1 + 1*2 + 1*1) / 16 = 5/16; kappa = (12/16 - 5/16) / (11/16) = 7/11
    a = ["digital", "digital", "eletromecanico", "ocorrencia"]
    b = ["digital", "eletromecanico", "eletromecanico", "ocorrencia"]
    assert kappa_cohen(a, b) == pytest.approx(7 / 11)


def test_kappa_limites():
    assert kappa_cohen([1, 0, 1], [1, 0, 1]) == pytest.approx(1.0)
    # discordancia total com marginais equilibradas: po = 0, pe = 1/2
    assert kappa_cohen([1, 0], [0, 1]) == pytest.approx(-1.0)
    # os dois usaram uma unica categoria: pe = 1, indefinido
    assert kappa_cohen([0, 0, 0], [0, 0, 0]) is None
    assert kappa_cohen([], []) is None


def test_maioria_e_empate():
    assert maioria(["digital", "digital", "ocorrencia"]) == ("digital", False)
    assert maioria([1, 0]) == (None, True)
    assert maioria([1]) == (1, False)


def _voto(cena, legivel=0, **defeitos):
    v = {"cena": cena, "legivel": legivel}
    v |= {d: defeitos.get(d, 0) for d in DEFEITOS}
    return v


def test_consolidar_aplica_regra_de_cena_e_marca_empate():
    por_foto = {
        # empate em reflexo, mas nao_medidor zera defeitos e legivel: nao conta como empate
        1: {"x": _voto("nao_medidor", reflexo=1), "y": _voto("nao_medidor")},
        # ocorrencia impoe legivel = 0 mesmo com voto 1 x 0
        2: {"x": _voto("ocorrencia", legivel=1), "y": _voto("ocorrencia")},
        # empate de cena: fica NA e a regra nao se aplica
        3: {"x": _voto("digital", legivel=1), "y": _voto("eletromecanico", legivel=1)},
        # maioria de 3
        4: {
            "x": _voto("digital", legivel=1),
            "y": _voto("digital", legivel=0),
            "z": _voto("digital", legivel=1),
        },
    }
    c = {item.foto: item for item in consolidar(por_foto)}
    assert not c[1].empate and c[1].valores["reflexo"] == 0 and c[1].valores["legivel"] == 0
    assert not c[2].empate and c[2].valores["legivel"] == 0
    assert c[3].empates == {"cena"} and c[3].valores["cena"] is None
    assert c[3].valores["legivel"] == 1
    assert not c[4].empate and c[4].valores["legivel"] == 1 and c[4].n_rotuladores == 3


def test_kappa_medio_pondera_por_n_e_ignora_indefinido():
    sem = dict.fromkeys(CLASSES)
    pares = [
        KappaPar("a", "b", 10, sem | {"cena": 0.8, "legivel": None}),
        KappaPar("a", "c", 30, sem | {"cena": 0.4, "legivel": 0.7}),
    ]
    medio = kappa_medio(pares)
    # (10*0,8 + 30*0,4) / 40 = 0,5
    assert medio["cena"].kappa == pytest.approx(0.5) and medio["cena"].n == 40
    assert not medio["cena"].passa
    assert medio["legivel"].kappa == pytest.approx(0.7) and medio["legivel"].n == 30
    assert medio["legivel"].passa
    assert medio["reflexo"].kappa is None and not medio["reflexo"].passa


def _por_foto_lab02() -> dict[str, dict[str, dict]]:
    por_foto: dict[str, dict[str, dict]] = defaultdict(dict)
    with (RAIZ_REPO / "lab-02/rotulos/rotulos.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            assert r["pulada"] == "0"
            por_foto[r["nome_arquivo"]][r["rotulador"]] = _voto(
                r["cena"], int(r["legivel"]), **{d: int(r[d]) for d in DEFEITOS}
            )
    return por_foto


def test_kappa_dos_500_rotulos_do_lab02_bate_com_resumo_json():
    por_foto = _por_foto_lab02()
    esperado = json.loads((RAIZ_REPO / "lab-02/resultados/resumo.json").read_text())

    pares = kappa_por_par(por_foto)
    medio = kappa_medio(pares)

    assert len(por_foto) == esperado["fotos_rotuladas"] == 329
    assert sum(1 for v in por_foto.values() if len(v) >= 2) == esperado["fotos_dupla_rotulagem"]
    assert [(p.a, p.b, p.n) for p in pares] == [
        ("davi_melo", "elizabete_barbosa", 83),
        ("davi_melo", "ivan", 56),
        ("elizabete_barbosa", "ivan", 86),
    ]
    for classe, kappa in esperado["kappa"].items():
        assert round(medio[classe].kappa, 3) == kappa, classe
    assert {c for c in CLASSES if medio[c].passa} == {"cena", "legivel"}


def test_relatorio_kappa_md_traz_gate_desvios_e_aceite_pendente():
    por_foto = _por_foto_lab02()
    pares = kappa_por_par(por_foto)
    md = relatorio.gerar(
        [RodadaRotulagem.LAB02],
        kappa_medio(pares),
        pares,
        {"lab02": 500},
        rotulos=500,
        fotos=329,
        fotos_dupla=117,
        fotos_empate=39,
        controle_com_dupla=50,
    )
    assert "| cena | 0,744 | 225 | passa |" in md
    assert "| tampa_suja | 0,148 | 225 | **não passa** (< 0,6) |" in md
    assert "| davi_melo × elizabete_barbosa | 83 | 0,780 |" in md
    assert "Fotos de controle com 2 ou mais rótulos: 50." in md
    assert "5 de 7 classes ficam abaixo de 0,6" in md
    assert "As rodadas A e B com o esquema v2 ainda não rodaram" in md
    assert "| a | 0 |" in md and "| lab02 | 500 |" in md
    for trecho in (
        "## Desvios",
        "Decisão pendente do Davi",
        "3 votos",
        "não parquet",
        "POST /rotulagem/proxima",
        "## Risco residual",
        "T-M6-02",
        "o kappa das rodadas cegas é só para admin, e admin\n   não rotula nelas",
        "editada no lugar",
    ):
        assert trecho in md, trecho

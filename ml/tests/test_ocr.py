"""Metricas e regras do spike de OCR (M9). Valores ficticios, sem foto nem leitura real."""

import cv2
import numpy as np
import pandas as pd
import pytest

from mlinsp import ocr


@pytest.mark.parametrize("texto, esperado", [
    ("001234.5", "1234"),
    ("001234,5", "1234"),
    ("0000", "0"),
    (" 12 34 ", "1234"),
    ("abc", ""),
    ("", ""),
    (None, ""),
])
def test_normalizar_leitura(texto, esperado):
    assert ocr.normalizar_leitura(texto) == esperado


@pytest.mark.parametrize("a, b, d", [
    ("", "", 0), ("1234", "1234", 0), ("1234", "1243", 2), ("1234", "123", 1),
    ("", "987", 3), ("55555", "5555", 1), ("1111", "7777", 4),
])
def test_distancia_edicao(a, b, d):
    assert ocr.distancia_edicao(a, b) == d
    assert ocr.distancia_edicao(b, a) == d


def test_comparar_casos():
    c = ocr.comparar("004321", "4321")
    assert c["exato"] and c["ultimos4"] and c["distancia"] == 0
    c = ocr.comparar("19876", "29876")  # erra so o primeiro digito
    assert not c["exato"] and c["ultimos4"] and c["distancia"] == 1
    c = ocr.comparar("43215", "4321")  # casa decimal lida como inteira
    assert c["digito_a_mais"] and not c["ultimos4"]
    c = ocr.comparar("", "4321")
    assert not c["exato"] and not c["ultimos4"] and c["distancia"] == 4
    c = ocr.comparar("7", "00007")  # leitura curta: ultimos 4 com zero a esquerda
    assert c["exato"] and c["ultimos4"]
    with pytest.raises(ValueError):
        ocr.comparar("123", "abc")


def test_wilson_contem_a_proporcao_e_fica_em_0_1():
    lo, hi = ocr.wilson(7, 10)
    assert 0 <= lo < 0.7 < hi <= 1
    lo, hi = ocr.wilson(0, 50)
    assert lo == pytest.approx(0, abs=1e-12) and 0 < hi < 0.1
    assert all(np.isnan(ocr.wilson(0, 0)))


def test_resumir_agrega_por_abordagem_e_cena():
    linhas = []
    for abordagem, previstos in (("a", ["1111", "2222", "", "4440"]), ("b", ["1111"] * 4)):
        for prev, gab, cena in zip(previstos, ["1111", "2222", "3333", "4444"],
                                   ["digital", "digital", "eletromecanico", "eletromecanico"]):
            linhas.append({"abordagem": abordagem, "cena": cena, "segundos": 0.5,
                           **ocr.comparar(prev, gab)})
    tab = ocr.resumir(pd.DataFrame(linhas)).set_index("abordagem")
    assert tab.loc["a", "acerto_exato"] == pytest.approx(0.5)
    assert tab.loc["a", "sem_leitura"] == pytest.approx(0.25)
    assert tab.loc["a", "distancia_media"] == pytest.approx((0 + 0 + 4 + 1) / 4)
    assert tab.loc["b", "acerto_exato"] == pytest.approx(0.25)
    por_cena = ocr.resumir(pd.DataFrame(linhas), por=["cena"]).set_index(["abordagem", "cena"])
    assert por_cena.loc[("a", "digital"), "acerto_exato"] == pytest.approx(1.0)
    assert por_cena.loc[("a", "eletromecanico"), "n"] == 2


def test_escolher_leitura_prefere_texto_alto_e_filtra_tamanho():
    C = ocr.Candidato
    candidatos = [
        C("03", 0.99, 10),          # codigo do display: curto demais
        C("240", 0.95, 12),         # tensao: curto demais
        C("3210987654", 0.9, 30),   # numero de serie: longo demais
        C("012345", 0.40, 25),      # leitura: maior altura entre os validos
        C("2019", 0.90, 8),         # ano da placa: valido, mas baixo
    ]
    assert ocr.escolher_leitura(candidatos) == "012345"
    assert ocr.escolher_leitura([C("03", 0.9, 10)]) == ""
    assert ocr.escolher_leitura([C("1234", 0.2, 10), C("5678", 0.8, 10)]) == "5678"


def test_recortes_e_caixa():
    img = np.arange(100 * 200).reshape(100, 200)
    assert ocr.recorte_central(img, 0.5).shape == (50, 100)
    assert ocr.recortar_caixa(img, (10, 20, 50, 40), margem=0).shape == (20, 40)
    assert ocr.recortar_caixa(img, (0, 0, 200, 100), margem=0.5).shape == (100, 200)
    with pytest.raises(ValueError):
        ocr.recortar_caixa(img, (50, 20, 10, 40))
    assert ocr.ler_caixa("1,2,30,40") == (1, 2, 30, 40)
    assert ocr.ler_caixa("") is None and ocr.ler_caixa(float("nan")) is None
    with pytest.raises(ValueError):
        ocr.ler_caixa("1,2,3")


def test_preparar_tesseract_deixa_texto_escuro_em_fundo_claro():
    # display escuro com digito claro: deve sair invertido (fundo branco dominante)
    rgb = np.zeros((40, 120, 3), np.uint8)
    cv2.putText(rgb, "1234", (5, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)
    out = ocr.preparar_tesseract(rgb, altura_alvo=120)
    assert out.shape[0] == 120 and set(np.unique(out)) <= {0, 255}
    assert (out == 255).mean() > 0.5


def test_recorte_tesseract_tenta_as_duas_polaridades(monkeypatch):
    """Sem depender do binario: o motor falso so 'le' quando o fundo e branco."""
    vistos = []

    def falso(img, psm):
        vistos.append(((img == 255).mean() > 0.5, psm))
        return [ocr.Candidato("04817", 0.6, 40.0)] if (img == 255).mean() > 0.5 and psm == 7 else []

    monkeypatch.setattr(ocr, "tesseract_candidatos", falso)
    rgb = np.zeros((60, 260, 3), np.uint8)
    cv2.putText(rgb, "48", (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
    assert ocr.ler_recorte_tesseract(rgb, (0, 0, 260, 60)) == "04817"
    assert {p for _, p in vistos} == {7, 11} and {b for b, _ in vistos} == {True, False}

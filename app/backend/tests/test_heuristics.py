"""Heuristicas do M5: paridade com o `mlinsp` (Lab 2), scores, detector e politica.

Os valores de OURO_MLINSP foram gerados por `ml/src/mlinsp/heuristicas.py`
(`features_v0`, que recebe RGB) sobre as imagens sinteticas de `imagens()`. Se
este teste quebrar, o backend deixou de calcular o mesmo numero que o notebook.
A paridade nas 12.340 fotos reais esta medida em ml/reports/heuristicas.md.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from inspecao.config import Settings
from inspecao.inference import heuristics
from inspecao.inference.detector import DetectorHeuristico, DetectorNulo, carregar_detector
from inspecao.inference.politica import (
    CLASSES,
    POLITICA_ATUAL,
    POLITICA_V0,
    POLITICA_V1,
    POLITICAS,
    Decisao,
    obter,
)


def imagens() -> dict[str, np.ndarray]:
    """BGR 480x360, sem aleatoriedade (o gerador de numeros nao entra na paridade)."""
    y, x = np.mgrid[0:480, 0:360].astype(np.float64)
    textura = 127 + 60 * np.sin(x / 7.0) + 50 * np.cos(y / 11.0)
    bgr = np.dstack([textura, np.roll(textura, 40, 0), 255 - textura]).clip(0, 255)
    bgr = bgr.astype(np.uint8)
    xadrez = (((x // 24) + (y // 24)) % 2 * 255).astype(np.uint8)
    xadrez = cv2.cvtColor(xadrez, cv2.COLOR_GRAY2BGR)
    return {
        "textura": bgr,
        "xadrez": xadrez,
        "xadrez_borrado": cv2.GaussianBlur(xadrez, (0, 0), 6),
        "escura": (bgr // 8).astype(np.uint8),
        "preta": np.zeros((480, 360, 3), np.uint8),
    }


OURO_MLINSP: dict[str, dict[str, float]] = {
    "textura": {
        "lap_var": 2.619478194625289,
        "lap_var_centro": 2.3021842399691357,
        "tenengrad": 755.9634027777778,
        "brilho_media": 126.89751736111111,
        "contraste": 31.483776882697338,
        "saturacao_media": 154.18269097222222,
        "frac_estourados": 0.0,
        "frac_escuros": 0.0,
        "entropia_hist": 6.892125836125244,
        "bordas_centro": 0.0,
        "p01": 67.0,
        "p99": 187.0,
    },
    "xadrez": {
        "lap_var": 11006.083333333334,
        "lap_var_centro": 12162.083333333334,
        "tenengrad": 153675.75,
        "brilho_media": 127.5,
        "contraste": 127.5,
        "saturacao_media": 0.0,
        "frac_estourados": 0.5,
        "frac_escuros": 0.5,
        "entropia_hist": 1.0,
        "bordas_centro": 0.07527777777777778,
        "p01": 0.0,
        "p99": 255.0,
    },
    "xadrez_borrado": {
        "lap_var": 6.132083333333333,
        "lap_var_centro": 6.33425925925926,
        "tenengrad": 7038.223703703704,
        "brilho_media": 127.5,
        "contraste": 60.58526972298966,
        "saturacao_media": 0.0,
        "frac_estourados": 0.0015509259259259259,
        "frac_escuros": 0.09689814814814815,
        "entropia_hist": 7.157195278395046,
        "bordas_centro": 0.0,
        "p01": 15.0,
        "p99": 240.0,
    },
    "escura": {
        "lap_var": 2.100202347735286,
        "lap_var_centro": 2.0617758053626543,
        "tenengrad": 14.636180555555555,
        "brilho_media": 15.420491898148148,
        "contraste": 3.968752105707124,
        "saturacao_media": 157.41770254629628,
        "frac_estourados": 0.0,
        "frac_escuros": 1.0,
        "entropia_hist": 3.9258314571834383,
        "bordas_centro": 0.0,
        "p01": 8.0,
        "p99": 23.0,
    },
    "preta": {
        "lap_var": 0.0,
        "lap_var_centro": 0.0,
        "tenengrad": 0.0,
        "brilho_media": 0.0,
        "contraste": 0.0,
        "saturacao_media": 0.0,
        "frac_estourados": 0.0,
        "frac_escuros": 1.0,
        "entropia_hist": -0.0,
        "bordas_centro": 0.0,
        "p01": 0.0,
        "p99": 0.0,
    },
}


@pytest.mark.parametrize("nome", sorted(OURO_MLINSP))
def test_sinais_identicos_ao_mlinsp(nome):
    obtido = heuristics.sinais(imagens()[nome])
    assert list(obtido) == list(heuristics.NOMES_V0)
    for feature, esperado in OURO_MLINSP[nome].items():
        assert obtido[feature] == pytest.approx(esperado, rel=1e-9, abs=1e-12), feature


def test_scores_cobrem_as_cinco_classes_da_politica_e_ficam_em_0_1():
    for imagem in imagens().values():
        s = heuristics.scores(heuristics.sinais(imagem))
        assert set(s) == set(CLASSES)
        assert all(0.0 <= v <= 1.0 for v in s.values())


def test_quadro_preto_e_lixo_e_foto_nitida_nao():
    preta = heuristics.scores(heuristics.sinais(imagens()["preta"]))
    assert preta["fora_de_foco"] == 1.0
    assert preta["display_apagado"] == 1.0
    xadrez = heuristics.scores(heuristics.sinais(imagens()["xadrez"]))
    assert max(xadrez.values()) == 0.0  # metade escura e metade estourada, mas nitido


def test_borrar_aumenta_o_score_de_foco():
    textura = imagens()["textura"]
    nitida = heuristics.scores(heuristics.sinais(textura))
    borrada = heuristics.scores(heuristics.sinais(cv2.GaussianBlur(textura, (0, 0), 4)))
    assert 0.0 < nitida["fora_de_foco"] < borrada["fora_de_foco"]


def test_nitidez_limita_as_outras_classes():
    """Escuro, estourado ou centro liso so contam com nitidez baixa (ver docstring)."""
    for imagem in imagens().values():
        s = heuristics.scores(heuristics.sinais(imagem))
        assert max(s.values()) == s["fora_de_foco"]


def test_rampa_nos_pontos_de_ancora():
    ok, lixo, log = heuristics.ESCALAS["tenengrad"]
    assert heuristics._rampa(ok, ok, lixo, log) == 0.0
    assert heuristics._rampa(lixo, ok, lixo, log) == 1.0
    assert heuristics._rampa(ok * 10, ok, lixo, log) == 0.0  # alem do ok, satura em 0
    assert 0.0 < heuristics._rampa(500.0, ok, lixo, log) < 1.0


def test_detector_heuristico_grava_scores_e_sinais():
    saida = DetectorHeuristico().avaliar(imagens()["textura"])
    assert set(saida) == set(CLASSES) | {"sinais"}
    assert set(saida["sinais"]) == set(heuristics.NOMES_V0)
    assert DetectorHeuristico.nome == "heuristica@v1"


def test_carregar_detector_por_configuracao():
    assert isinstance(
        carregar_detector(Settings(detector_qualidade="heuristica")), DetectorHeuristico
    )
    assert isinstance(carregar_detector(Settings(detector_qualidade="nulo")), DetectorNulo)
    with pytest.raises(RuntimeError, match="DETECTOR_QUALIDADE"):
        carregar_detector(Settings(detector_qualidade="inexistente"))
    # Sem o .onnx no caminho configurado, o worker nao sobe (nao cai em outro detector).
    with pytest.raises(RuntimeError, match="ausente"):
        carregar_detector(
            Settings(modelo_qualidade_ativo=True, modelo_qualidade_path=Path("/nao/existe.onnx"))
        )


def test_politica_v1_so_rejeita_acima_do_limiar_e_nunca_aprova():
    p = POLITICA_V1
    assert p.limiar_aprova is None
    assert p.modelo == DetectorHeuristico.nome
    assert p.decidir({"fora_de_foco": p.limiar_rejeita}) == (Decisao.REJEITAR, p.limiar_rejeita)
    abaixo = p.limiar_rejeita - 1e-6
    assert p.decidir({"fora_de_foco": abaixo, "glare": 0.0}) == (Decisao.HUMANO, abaixo)
    assert p.decidir({"glare": 0.0}) == (Decisao.HUMANO, 0.0)  # score zero nao aprova


def test_politica_ignora_chaves_que_nao_sao_classe():
    saida = {"fora_de_foco": 0.1, "sinais": {"tenengrad": 1.0e9}, "outra": 0.99}
    assert POLITICA_V1.decidir(saida) == (Decisao.HUMANO, 0.1)
    assert POLITICA_V1.decidir({"sinais": {}}) == (Decisao.HUMANO, None)


def test_politica_v0_manda_tudo_para_humano():
    assert POLITICA_V0.decidir({"fora_de_foco": 1.0}) == (Decisao.HUMANO, 1.0)
    assert POLITICA_V0.decidir({}) == (Decisao.HUMANO, None)
    assert POLITICA_V0.aceita("qualquer@v9")


def test_registro_de_politicas():
    assert POLITICA_ATUAL is POLITICA_V1
    assert obter("politica@v0") is POLITICA_V0
    assert set(POLITICAS) == {"politica@v0", "politica@v1", "politica@v2"}
    assert not POLITICA_V1.aceita("nulo@v0")
    with pytest.raises(LookupError):
        obter("politica@v9")

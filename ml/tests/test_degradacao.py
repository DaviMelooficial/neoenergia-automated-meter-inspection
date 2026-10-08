import numpy as np
import pytest

from mlinsp.dados import DEFEITOS
from mlinsp.degradacao import (
    TRANSFORMACOES,
    degradar,
    eh_fonte_limpa,
    gerar_sintetico,
    rotulo_apos_degradacao,
)


def _foto(seed=0):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (480, 360, 3), dtype=np.uint8)
    img[200:260, 100:260] = 30  # um "display" escuro com borda nitida
    return img


@pytest.mark.parametrize("defeito", DEFEITOS)
def test_cada_transformacao_e_deterministica_com_seed(defeito):
    img = _foto()
    for fn in TRANSFORMACOES[defeito]:
        a = fn(img, np.random.default_rng(7))
        b = fn(img, np.random.default_rng(7))
        assert a.shape == img.shape and a.dtype == np.uint8
        assert np.array_equal(a, b)
        assert not np.array_equal(a, img)


def test_degradar_e_gerar_sintetico_sao_reprodutiveis():
    img = _foto()
    assert np.array_equal(degradar(img, ["reflexo", "fora_de_foco"], 3),
                          degradar(img, ["reflexo", "fora_de_foco"], 3))
    assert not np.array_equal(degradar(img, ["fora_de_foco"], 3), degradar(img, ["fora_de_foco"], 4))
    a, b = gerar_sintetico(img, seed=11), gerar_sintetico(img, seed=11)
    assert [r for _, r in a] == [r for _, r in b]
    assert all(np.array_equal(x, y) for (x, _), (y, _) in zip(a, b))
    assert a[0][1] == {**{d: 0 for d in DEFEITOS}, "legivel": 1}
    assert all(1 <= sum(r[d] for d in DEFEITOS) <= 2 and r["legivel"] == 0 for _, r in a[1:])


def test_borrao_reduz_nitidez():
    import cv2

    img = _foto()
    borrada = degradar(img, ["fora_de_foco"], 5)
    nit = lambda x: cv2.Laplacian(cv2.cvtColor(x, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var()
    assert nit(borrada) < nit(img) / 2


def test_regra_foto_ja_com_defeito():
    real = {"cena": "digital", **{d: 0 for d in DEFEITOS}, "fora_de_foco": 1, "legivel": 0}
    out = rotulo_apos_degradacao(real, ["fora_de_foco", "reflexo"])
    assert out["fora_de_foco"] == 1 and out["reflexo"] == 1 and out["legivel"] == 0
    assert not eh_fonte_limpa(real)
    limpa = {"cena": "digital", **{d: 0 for d in DEFEITOS}, "legivel": 1}
    assert eh_fonte_limpa(limpa)
    assert not eh_fonte_limpa({**limpa, "cena": "ocorrencia"})
    assert rotulo_apos_degradacao(limpa, [])["legivel"] == 1

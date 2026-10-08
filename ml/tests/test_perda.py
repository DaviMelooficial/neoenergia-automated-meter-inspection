import math

import numpy as np
import pytest
import torch

from mlinsp.modelo import perda_triagem, pesos_desbalanceamento


def _bce(z, y):
    return math.log1p(math.exp(-z)) if y == 1 else math.log1p(math.exp(z))


def test_perda_triagem_ignora_ausentes_contra_conta_a_mao():
    saidas = {
        "cena": torch.tensor([[2.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [5.0, 5.0, 5.0, 5.0]]),
        "defeitos": torch.tensor([[1.0, -1.0, 0.0, 0.0, 0.0],
                                  [0.5, 0.0, 0.0, 0.0, 0.0],
                                  [9.0, 9.0, 9.0, 9.0, 9.0]]),
        "legivel": torch.tensor([2.0, -3.0, 7.0]),
    }
    nan = float("nan")
    alvos = {
        "cena": torch.tensor([0, 2, -1]),  # 3a foto: cena empatada (ignore_index=-1)
        "defeitos": torch.tensor([[1.0, 0.0, nan, nan, nan],
                                  [0.0, nan, nan, nan, nan],
                                  [nan] * 5]),
        "legivel": torch.tensor([1.0, nan, 0.0]),
    }
    # CE media sobre as 2 fotos com cena
    ce1 = -2.0 + math.log(math.exp(2) + 3)
    ce2 = -0.0 + math.log(math.e + 3)
    ce = (ce1 + ce2) / 2
    # BCE de defeitos: media sobre os 3 alvos presentes
    bce_d = (_bce(1.0, 1) + _bce(-1.0, 0) + _bce(0.5, 0)) / 3
    # BCE de legivel: fotos 1 e 3
    bce_l = (_bce(2.0, 1) + _bce(7.0, 0)) / 2
    perda = perda_triagem(saidas, alvos)
    assert perda.item() == pytest.approx(ce + bce_d + bce_l, rel=1e-5)
    # lam pondera so as BCE
    assert perda_triagem(saidas, alvos, lam=0.5).item() == pytest.approx(
        ce + 0.5 * (bce_d + bce_l), rel=1e-5)


def test_perda_so_com_cabecas_presentes():
    saidas = {"legivel": torch.tensor([0.0, 0.0])}
    alvos = {"legivel": torch.tensor([1.0, 0.0])}
    assert perda_triagem(saidas, alvos).item() == pytest.approx(math.log(2), rel=1e-6)


def test_pesos_desbalanceamento_caso_conhecido():
    nan = np.nan
    alvos = {
        "cena": np.array([0, 0, 0, 1, -1, 0]),  # 4 digital, 1 eletromecanico, 1 ausente
        "defeitos": np.array([[1, 0, 0, 0, 0],
                              [0, 0, 0, 0, 0],
                              [0, 0, 0, 0, 0],
                              [0, 0, 0, 0, 0],
                              [0, 0, 0, 0, 0],
                              [nan, 1, 0, 0, 0]], dtype=np.float32),
        "legivel": np.array([1, 1, 1, 0, nan, 1], dtype=np.float32),
    }
    peso_cena, pw, pw_leg = pesos_desbalanceamento(alvos, teto=20.0)
    # total 5, 2 classes presentes: peso = 5 / cont / 2; ausentes = 0
    assert peso_cena.tolist() == pytest.approx([5 / 4 / 2, 5 / 1 / 2, 0.0, 0.0])
    # reflexo: 1 pos, 4 neg (o NaN nao conta) -> 4; fora_de_foco: 1 pos, 5 neg -> 5;
    # sem positivo -> 1
    assert pw.tolist() == pytest.approx([4.0, 5.0, 1.0, 1.0, 1.0])
    # legivel: 1 neg / 4 pos
    assert pw_leg.item() == pytest.approx(0.25)
    _, pw_teto, _ = pesos_desbalanceamento(alvos, teto=4.5)
    assert pw_teto.tolist() == pytest.approx([4.0, 4.5, 1.0, 1.0, 1.0])

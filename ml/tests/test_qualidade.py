"""M7: letterbox, rotulos humanos, limiar de rejeicao, metricas e exportacao ONNX (sem fotos)."""

from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd
import pytest
import torch

from mlinsp import modelo, qualidade
from mlinsp.calibrate import limiar_zero_fp
from mlinsp.evaluate import cobertura_maxima_zero_fp, metricas_legivel, rejeicao, wilson
from mlinsp.export_onnx import exportar, paridade, versao_ja_usada

RAIZ = Path(__file__).resolve().parents[2]


def test_letterbox_mantem_proporcao_e_preenche_com_a_media():
    rgb = np.full((480, 360, 3), 255, np.uint8)
    lb = qualidade.letterbox(rgb)
    assert lb.shape == (224, 224, 3)
    assert (lb[:, :28] == qualidade.PREENCHIMENTO).all()
    assert (lb[:, 196:] == qualidade.PREENCHIMENTO).all()
    assert (lb[:, 28:196] == 255).all()
    x = qualidade.normalizar(qualidade.para_tensor([lb]))
    assert np.abs(x[0, :, :, :28].numpy()).max() < 0.02  # media do ImageNet ~ 0


def test_rotulos_humanos_maioria_empate_e_legivel_algum(tmp_path):
    cab = ("nome_arquivo,lote,rotulador,cena,reflexo,fora_de_foco,tampa_suja,enquadramento,"
           "display_apagado,legivel,pulada,segundos_na_foto,gravado_em\n")
    linhas = [
        "a.jpg,1,x,digital,0,0,0,0,0,1,0,1,t", "a.jpg,1,y,digital,0,1,0,0,0,0,0,1,t",
        "b.jpg,1,x,digital,0,0,0,0,0,1,0,1,t", "b.jpg,1,y,digital,0,0,0,0,0,1,0,1,t",
        "b.jpg,1,z,digital,1,0,0,0,0,0,0,1,t",
        "c.jpg,1,x,nao_medidor,0,0,0,0,0,0,0,1,t",
    ]
    arq = tmp_path / "rotulos.csv"
    arq.write_text(cab + "\n".join(linhas) + "\n")
    rot = qualidade.rotulos_humanos(RAIZ, arq)
    assert np.isnan(rot.at["a.jpg", "legivel"])  # empate 1 x 0
    assert rot.at["a.jpg", "legivel_algum"] == 1
    assert rot.at["b.jpg", "legivel"] == 1.0 and rot.at["b.jpg", "n_rotuladores"] == 3
    assert rot.at["c.jpg", "legivel"] == 0.0 and rot.at["c.jpg", "legivel_algum"] == 0
    alvos = qualidade.alvos_humanos(rot)
    assert np.isnan(alvos["defeitos"]).all()  # defeito humano nunca vira alvo


def test_limiar_zero_fp_fica_acima_da_foto_lida_mais_alta():
    p = np.array([0.1, 0.95, 0.97, 0.9812, 0.99, 0.999])
    lida = np.array([0, 1, 0, 0, 0, 0])
    r = limiar_zero_fp(p, lida)
    assert r["maior_p_de_lida"] == 0.95
    assert r["limiar"] == 0.97  # menor rejeitavel acima da lida, arredondado para cima
    assert r["rejeitadas_val"] == 4
    assert limiar_zero_fp(p, np.ones(6, int))["limiar"] is None  # nada a rejeitar


def test_arredondamento_nunca_fica_abaixo_do_valor_observado():
    r = limiar_zero_fp(np.array([0.5, 0.97527]), np.array([1, 0]))
    assert r["limiar"] == 0.976 and r["rejeitadas_val"] == 0  # o lado conservador


def test_wilson_e_metricas_de_rejeicao():
    assert wilson(0, 10) == [0.0, 0.2775]
    assert wilson(10, 10) == [0.7225, 1.0]
    p = np.array([0.99, 0.98, 0.2, 0.1])
    r = rejeicao(p, np.array([0, 1, 0, 1]), 0.975)
    assert r["rejeitadas"] == 2 and r["falsos_positivos"] == 1 and r["precisao"] == 0.5
    assert rejeicao(p, np.zeros(4, int), None)["rejeitadas"] == 0
    assert cobertura_maxima_zero_fp(p, np.array([0, 1, 0, 1])) == 0.25


def test_metricas_legivel_por_classe():
    p_il = np.array([0.9, 0.8, 0.2, 0.1, 0.7])
    y_leg = np.array([0, 0, 1, 1, np.nan])
    m = metricas_legivel(p_il, y_leg, 0.5)
    assert m["n"] == 4 and m["macro_f1"] == 1.0 and m["pr_auc_ilegivel"] == 1.0
    assert m["matriz_confusao"]["valores"] == [[2, 0], [0, 2]]


def test_treino_reprodutivel_com_a_mesma_semente():
    """Mesma semente, mesmos dados -> mesmos logits (aceite do M7), em CPU e em miniatura."""
    rng = np.random.default_rng(0)
    imgs = torch.from_numpy(rng.integers(0, 256, (8, 3, 64, 64), dtype=np.uint8))
    sint = qualidade.Sintetico(imgs, rng.integers(0, 2, (8, 5)).astype(np.float32), ["f"] * 8)
    rot = pd.DataFrame({"cena": ["digital", "eletromecanico"] * 4,
                        "legivel": [1.0, 0.0] * 4})
    cfg = qualidade.Config(lote=4, epocas_1=1, epocas_2=2, min_epocas_2=1)
    cpu = torch.device("cpu")
    saidas = []
    for _ in range(2):
        m, _ = qualidade.treinar(1, cfg, sint, imgs, qualidade.alvos_humanos(rot), imgs,
                                 rot["legivel"].to_numpy(np.float32), cpu, pretreinado=False)
        saidas.append(qualidade.prever(m, imgs, cpu)["legivel"])
    assert np.array_equal(saidas[0], saidas[1])


def test_exportacao_onnx_com_paridade(tmp_path):
    modelo.semear(0)
    m = modelo.ModeloTriagem(pretreinado=False).eval()
    destino = tmp_path / "q.onnx"
    exportar(m, destino)
    sess = ort.InferenceSession(str(destino), providers=["CPUExecutionProvider"])
    assert [o.name for o in sess.get_outputs()] == ["cena", "defeitos", "legivel"]
    imgs = torch.from_numpy(
        np.random.default_rng(1).integers(0, 256, (3, 3, 224, 224), dtype=np.uint8))
    par = paridade(m, destino, imgs)
    assert par["criterio"] < 1e-4
    saida = sess.run(None, {"entrada": qualidade.normalizar(imgs).numpy()})
    assert [s.shape for s in saida] == [(3, 4), (3, 5), (3,)]


@pytest.mark.parametrize("d", qualidade.DEFEITOS)
def test_todo_defeito_tem_classe_no_backend(d):
    assert qualidade.DEFEITO_PARA_CLASSE[d] in {
        "fora_de_foco", "glare", "tampa_suja", "enquadramento_incorreto", "display_apagado"}


def test_rodada_nova_nao_reusa_versao_ja_exportada(tmp_path):
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "qualidade.json").write_text('{"versao": "qualidade@v1", "rodada": "r1"}')
    assert versao_ja_usada(tmp_path, "qualidade@v1", "r2") == "r1"
    assert versao_ja_usada(tmp_path, "qualidade@v1", "r1") is None  # reexportar a mesma
    assert versao_ja_usada(tmp_path, "qualidade@v2", "r2") is None

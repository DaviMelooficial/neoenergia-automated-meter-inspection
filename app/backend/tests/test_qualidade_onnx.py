"""DetectorOnnx (M7) com um modelo ONNX minusculo gerado aqui, sem foto real nem torch.

O modelo de teste tira a media de cada canal da entrada e aplica pesos fixos: o logit de
`legivel` e 100 x a media do canal R normalizado, entao quadro claro sai legivel e quadro
preto sai ilegivel. O pre-processamento e conferido contra valores gerados por
`ml/src/mlinsp/qualidade.py` (letterbox + normalizar), que e o que o modelo real viu.
"""

import hashlib
import json

import cv2
import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper
from sqlalchemy import select

from inspecao.config import Settings, get_settings
from inspecao.enums import StatusLeitura
from inspecao.inference.detector import carregar_detector
from inspecao.inference.politica import (
    CLASSES,
    POLITICA_V0,
    POLITICA_V1,
    POLITICA_V2,
    Decisao,
    politica_para,
)
from inspecao.inference.qualidade_onnx import DetectorOnnx, preprocessar
from inspecao.jobs import fila
from inspecao.jobs.handlers import Contexto
from inspecao.jobs.worker import processar_um
from inspecao.models import Foto, Leitura, ResultadoInferencia
from tests import lote_sintetico as ls

CENAS = ["digital", "eletromecanico", "ocorrencia", "nao_medidor"]
DEFEITOS = ["reflexo", "fora_de_foco", "tampa_suja", "enquadramento", "display_apagado"]
ENTRADA = {
    "nome": "entrada",
    "layout": "NCHW",
    "dtype": "float32",
    "cores": "RGB",
    "lado": 224,
    "preenchimento_rgb": [124, 116, 104],
    "escala": 1 / 255,
    "media": [0.485, 0.456, 0.406],
    "desvio": [0.229, 0.224, 0.225],
}

# mlinsp.qualidade.normalizar(letterbox(textura())) para a textura abaixo (em RGB).
OURO_ML = {
    "soma": 24542.989614271093,
    "pixels": {
        (0, 0, 0): 0.005565485917031765,
        (1, 100, 100): -0.8102239966392517,
        (2, 223, 223): 0.008191797882318497,
        (0, 112, 27): 0.005565485917031765,
        (0, 112, 28): -0.7136741280555725,
        (2, 50, 195): 0.3916340470314026,
    },
}


def textura_rgb() -> np.ndarray:
    y, x = np.mgrid[0:480, 0:360].astype(np.float64)
    t = 127 + 60 * np.sin(x / 7.0) + 50 * np.cos(y / 11.0)
    return np.dstack([t, np.roll(t, 40, 0), 255 - t]).clip(0, 255).astype(np.uint8)


def modelo_minusculo() -> onnx.ModelProto:
    """entrada (N,3,224,224) -> media por canal (N,3) -> cena (N,4), defeitos (N,5),
    legivel (N,). Mesmos nomes e formas do modelo exportado pelo ml."""
    w_cena = np.array([[1, 0, 0, -1], [0, 1, 0, 0], [0, 0, 1, 0]], np.float32)
    w_def = np.array([[-1, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 1, 0, 0]], np.float32)
    w_leg = np.array([[100], [0], [0]], np.float32)
    nos = [
        helper.make_node("ReduceMean", ["entrada"], ["media"], axes=[2, 3], keepdims=0),
        helper.make_node("MatMul", ["media", "w_cena"], ["cena"]),
        helper.make_node("MatMul", ["media", "w_def"], ["defeitos"]),
        helper.make_node("MatMul", ["media", "w_leg"], ["leg2d"]),
        helper.make_node("ReduceSum", ["leg2d", "eixo"], ["legivel"], keepdims=0),
    ]
    grafo = helper.make_graph(
        nos,
        "qualidade_teste",
        [helper.make_tensor_value_info("entrada", TensorProto.FLOAT, ["lote", 3, 224, 224])],
        [
            helper.make_tensor_value_info("cena", TensorProto.FLOAT, ["lote", 4]),
            helper.make_tensor_value_info("defeitos", TensorProto.FLOAT, ["lote", 5]),
            helper.make_tensor_value_info("legivel", TensorProto.FLOAT, ["lote"]),
        ],
        initializer=[
            numpy_helper.from_array(w_cena, "w_cena"),
            numpy_helper.from_array(w_def, "w_def"),
            numpy_helper.from_array(w_leg, "w_leg"),
            numpy_helper.from_array(np.array([1], np.int64), "eixo"),
        ],
    )
    m = helper.make_model(grafo, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.checker.check_model(m)
    return m


@pytest.fixture
def modelo_onnx(tmp_path):
    caminho = tmp_path / "qualidade.onnx"
    caminho.write_bytes(modelo_minusculo().SerializeToString())
    contrato = {
        "versao": "qualidade@v1",
        "onnx_sha256": hashlib.sha256(caminho.read_bytes()).hexdigest(),
        "entrada": ENTRADA,
        "saidas": {"cena": CENAS, "defeitos": DEFEITOS, "legivel": ["legivel"]},
        "defeito_para_classe": {
            "reflexo": "glare",
            "fora_de_foco": "fora_de_foco",
            "tampa_suja": "tampa_suja",
            "enquadramento": "enquadramento_incorreto",
            "display_apagado": "display_apagado",
        },
        "temperatura_legivel": 2.0,
        "temperatura_cena": 1.0,
    }
    caminho.with_suffix(".json").write_text(json.dumps(contrato))
    return caminho


def test_preprocessamento_igual_ao_do_ml():
    x = preprocessar(textura_rgb()[..., ::-1].copy(), ENTRADA)  # o backend recebe BGR
    assert x.shape == (1, 3, 224, 224) and x.dtype == np.float32
    assert float(x.astype(np.float64).sum()) == pytest.approx(OURO_ML["soma"], abs=1e-6)
    for (c, i, j), esperado in OURO_ML["pixels"].items():
        assert float(x[0, c, i, j]) == pytest.approx(esperado, abs=1e-6), (c, i, j)


def test_letterbox_mantem_a_proporcao_e_preenche_com_a_media():
    x = preprocessar(np.full((480, 360, 3), 255, np.uint8), ENTRADA)[0]
    # 480x360 -> 224x168 centralizado: faixas de 28 px nas laterais.
    assert np.allclose(x[:, :, :28], x[:, :1, :1], atol=0)
    assert np.allclose(x[:, :, 196:], x[:, :1, :1], atol=0)
    branco = (1.0 - np.array(ENTRADA["media"])) / np.array(ENTRADA["desvio"])
    assert np.allclose(x[:, 112, 28:196], branco[:, None], atol=1e-5)


def test_saida_tem_ilegivel_as_cinco_classes_e_cena(modelo_onnx):
    det = DetectorOnnx(modelo_onnx)
    assert det.nome == "qualidade@v1"
    saida = det.avaliar(cv2.cvtColor(textura_rgb(), cv2.COLOR_RGB2BGR))
    assert set(saida) == {"ilegivel", *CLASSES, "cena"}
    assert all(0.0 <= saida[k] <= 1.0 for k in ("ilegivel", *CLASSES))
    assert set(saida["cena"]) == set(CENAS)
    assert sum(saida["cena"].values()) == pytest.approx(1.0)


def test_valores_saem_do_modelo_com_a_temperatura_do_contrato(modelo_onnx):
    det = DetectorOnnx(modelo_onnx)
    preto = np.zeros((480, 360, 3), np.uint8)
    x = preprocessar(preto, ENTRADA)
    r = float(x[0, 0].mean())
    esperado = 1 - 1 / (1 + np.exp(-(100 * r) / 2.0))  # temperatura_legivel = 2
    saida = det.avaliar(preto)
    assert saida["ilegivel"] == pytest.approx(esperado, rel=1e-5)
    assert saida["ilegivel"] > 0.99  # quadro preto
    assert det.avaliar(np.full((480, 360, 3), 255, np.uint8))["ilegivel"] < 0.01


def test_recusa_onnx_que_nao_bate_com_o_hash_do_contrato(modelo_onnx):
    contrato = json.loads(modelo_onnx.with_suffix(".json").read_text())
    contrato["onnx_sha256"] = "0" * 64
    modelo_onnx.with_suffix(".json").write_text(json.dumps(contrato))
    with pytest.raises(RuntimeError, match="sha256"):
        DetectorOnnx(modelo_onnx)


def test_recusa_modelo_sem_contrato(modelo_onnx):
    modelo_onnx.with_suffix(".json").unlink()
    with pytest.raises(RuntimeError, match="ausente"):
        DetectorOnnx(modelo_onnx)


def test_carregar_detector_onnx_por_configuracao(modelo_onnx):
    for s in (
        Settings(detector_qualidade="onnx", modelo_qualidade_path=modelo_onnx),
        Settings(modelo_qualidade_ativo=True, modelo_qualidade_path=modelo_onnx),
    ):
        assert isinstance(carregar_detector(s), DetectorOnnx)


def test_politica_v2_decide_so_por_ilegivel():
    p = POLITICA_V2
    assert p.modelo == "qualidade@v1" and p.chaves == ("ilegivel",)
    assert p.limiar_aprova is None
    defeitos_altos = dict.fromkeys(CLASSES, 1.0)
    abaixo = p.limiar_rejeita - 1e-6
    assert p.decidir({**defeitos_altos, "ilegivel": abaixo}) == (Decisao.HUMANO, abaixo)
    assert p.decidir({"ilegivel": p.limiar_rejeita}) == (Decisao.REJEITAR, p.limiar_rejeita)
    assert p.decidir({"ilegivel": 0.0, "cena": {"digital": 1.0}}) == (Decisao.HUMANO, 0.0)
    assert p.decidir(defeitos_altos) == (Decisao.HUMANO, None)  # sem a chave de decisao


def test_cada_detector_tem_a_sua_politica():
    assert politica_para("nulo@v0") is POLITICA_V0
    assert politica_para("heuristica@v1") is POLITICA_V1
    assert politica_para("qualidade@v1") is POLITICA_V2
    assert politica_para("desconhecido@v9") is POLITICA_V0  # sem calibracao: nunca rejeita
    assert POLITICA_V1.chaves == CLASSES  # a do M5 nao mudou


def test_triagem_com_detector_onnx_grava_e_decide_pela_v2(db_session, tmp_path, modelo_onnx):
    staging = ls.criar_staging(tmp_path / "staging")  # fotos pretas: ilegivel ~ 1
    _, lote, storage = ls.ingerir_sintetico(db_session, staging, tmp_path / "storage")
    det = DetectorOnnx(modelo_onnx)
    ctx = Contexto(
        settings=get_settings(), detector=det, storage=storage, politica=politica_para(det.nome)
    )
    while jobs := fila.reivindicar(db_session, "teste", lote=10):
        for job in jobs:
            assert processar_um(ctx, db_session, job), job.erro

    db_session.expire_all()
    status = db_session.scalars(
        select(Leitura.status_atual).where(
            Leitura.lote_id == lote.id, Leitura.linha_csv.in_(ls.LINHAS_COM_FOTO)
        )
    ).all()
    assert set(status) == {StatusLeitura.REJEITADA_QUALIDADE.value}
    leitura = db_session.scalar(
        select(Leitura).where(Leitura.lote_id == lote.id, Leitura.linha_csv == 2)
    )
    assert leitura.historico[-1].detalhe == {"politica": "politica@v2", "decisao": "rejeitar"}
    saida = db_session.scalar(
        select(ResultadoInferencia.saida).join(Foto).where(Foto.leitura_id == leitura.id)
    )
    assert saida["ilegivel"] > POLITICA_V2.limiar_rejeita and "cena" in saida

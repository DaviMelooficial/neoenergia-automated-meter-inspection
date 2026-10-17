"""Detector do modelo de qualidade treinado (marco M7), via onnxruntime. Sem torch.

O contrato vem de `qualidade.json`, gerado ao lado do `.onnx` por
`ml/src/mlinsp/export_onnx.py`: pre-processamento, nomes das saidas, temperaturas e o
sha256 do `.onnx`. O pre-processamento abaixo repete `mlinsp.qualidade.letterbox` e
`normalizar` passo a passo; a paridade de ponta a ponta (backend gravando no banco contra
o torch no ml) esta medida no report do M7.

Saida, no mesmo formato do heuristico (chaves de score no nivel de cima):
- `ilegivel`: 1 - sigmoid(legivel / T), o score de decisao da politica@v2;
- as 5 classes de qualidade a partir da cabeca de defeitos: informativas, treinadas so com
  degradacao sintetica (o rotulo humano de defeito tem kappa baixo). A politica@v2 nao as
  le; a tela da fila as mostra;
- `cena`: probabilidade de cada cena (dict, ignorado pela politica).
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _sigmoide(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max())
    return e / e.sum()


def preprocessar(bgr: np.ndarray, entrada: dict) -> np.ndarray:
    """BGR uint8 -> float32 (1, 3, lado, lado): RGB, letterbox, escala, media e desvio."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    lado = int(entrada["lado"])
    h, w = rgb.shape[:2]
    f = lado / max(h, w)
    nh, nw = round(h * f), round(w * f)
    peq = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    quadro = np.empty((lado, lado, 3), np.uint8)
    quadro[:] = entrada["preenchimento_rgb"]
    y0, x0 = (lado - nh) // 2, (lado - nw) // 2
    quadro[y0 : y0 + nh, x0 : x0 + nw] = peq
    # Divisao (e nao multiplicacao por 1/255): e a conta que o torch fez no treino, e
    # em float32 as duas diferem no ultimo bit.
    x = quadro.astype(np.float32) / np.float32(round(1 / entrada["escala"]))
    x = (x - np.asarray(entrada["media"], np.float32)) / np.asarray(entrada["desvio"], np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


class DetectorOnnx:
    """Carrega o modelo uma vez (no `carregar_detector` do worker) e roda em CPU."""

    def __init__(self, caminho_onnx: Path, threads: int = 1):
        import onnxruntime as ort

        caminho_onnx = Path(caminho_onnx)
        caminho_json = caminho_onnx.with_suffix(".json")
        if not caminho_onnx.is_file() or not caminho_json.is_file():
            raise RuntimeError(
                f"modelo de qualidade ausente: {caminho_onnx} e {caminho_json.name} "
                "(gere com `make m7-exportar`)"
            )
        self.contrato: dict[str, Any] = json.loads(caminho_json.read_text(encoding="utf-8"))
        obtido = _sha256(caminho_onnx)
        if obtido != self.contrato["onnx_sha256"]:
            raise RuntimeError(
                f"{caminho_onnx.name} nao e o modelo descrito em {caminho_json.name} "
                f"(sha256 {obtido[:12]} != {self.contrato['onnx_sha256'][:12]})"
            )
        self.nome: str = self.contrato["versao"]
        opcoes = ort.SessionOptions()
        # Lote 1 em CPU: 1 thread por worker e o mais rapido (medido no Lab 2) e nao
        # disputa nucleo com os outros workers.
        opcoes.intra_op_num_threads = threads
        opcoes.inter_op_num_threads = 1
        self.sessao = ort.InferenceSession(
            str(caminho_onnx), sess_options=opcoes, providers=["CPUExecutionProvider"]
        )
        self._saidas = [o.name for o in self.sessao.get_outputs()]
        self._entrada = self.contrato["entrada"]

    def avaliar(self, imagem: np.ndarray) -> dict[str, Any]:
        x = preprocessar(imagem, self._entrada)
        logits = dict(
            zip(self._saidas, self.sessao.run(None, {self._entrada["nome"]: x}), strict=True)
        )
        c = self.contrato
        p_legivel = float(_sigmoide(logits["legivel"][0] / c["temperatura_legivel"]).squeeze())
        p_defeitos = _sigmoide(logits["defeitos"][0])
        p_cena = _softmax(logits["cena"][0] / c["temperatura_cena"])
        return {
            "ilegivel": 1.0 - p_legivel,
            **{
                c["defeito_para_classe"][d]: float(p)
                for d, p in zip(c["saidas"]["defeitos"], p_defeitos, strict=True)
            },
            "cena": {nome: float(p) for nome, p in zip(c["saidas"]["cena"], p_cena, strict=True)},
        }

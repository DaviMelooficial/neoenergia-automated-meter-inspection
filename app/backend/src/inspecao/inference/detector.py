"""Contrato dos detectores de qualidade e a implementacao nula.

O detector so devolve scores. A decisao (rejeitar, seguir, humano) vive em
`inference.politica`, para que `recalcular_decisao` (M5) reaplique a politica
sem tocar em imagem.

| Implementacao       | Marco | Descricao                                          |
| DetectorNulo        | M3    | devolve {}, tudo vai para a fila humana            |
| DetectorHeuristico  | M5    | OpenCV, ver plano 04 e inference/heuristics.py     |
| DetectorOnnx        | M7    | modelo treinado via onnxruntime, qualidade_onnx.py |
"""

from typing import Any, Protocol, runtime_checkable

import numpy as np

from inspecao.config import Settings


@runtime_checkable
class Detector(Protocol):
    """`avaliar` devolve o que vai para `resultados_inferencia.saida`: scores em [0, 1]
    no nivel de cima (as classes de `politica.CLASSES` e, no modelo treinado, `ilegivel`)
    e, opcionalmente, outras chaves de diagnostico. Cada politica declara quais chaves le
    (`Politica.chaves`)."""

    nome: str  # ex.: "nulo@v0", "heuristica@v1", "qualidade@v2"

    def avaliar(self, imagem: np.ndarray) -> dict[str, Any]: ...


class DetectorNulo:
    """Nao olha a imagem. Serve para exercitar o fluxo inteiro antes de existir CV."""

    nome = "nulo@v0"

    def avaliar(self, imagem: np.ndarray) -> dict[str, Any]:
        return {}


class DetectorHeuristico:
    """Sinais classicos de OpenCV convertidos em scores de classe (plano 04, secao 2).

    Sem estado e sem modelo: as escalas vivem em `inference.heuristics`. Grava
    tambem os sinais brutos em `saida["sinais"]`, para auditoria e recalibracao.
    """

    nome = "heuristica@v1"

    def avaliar(self, imagem: np.ndarray) -> dict[str, Any]:
        from inspecao.inference import heuristics

        return heuristics.avaliar(imagem)


DETECTORES = ("nulo", "heuristica", "onnx")


def _escolha(settings: Settings) -> str:
    return "onnx" if settings.modelo_qualidade_ativo else settings.detector_qualidade


def carregar_detector(settings: Settings) -> Detector:
    """Escolhe o detector por configuracao. Chamado UMA vez por worker: o modelo ONNX
    e carregado aqui (com o contrato conferido contra a politica registrada, ver
    `politica.validar_contrato_onnx`) e reaproveitado em todos os jobs.

    MODELO_QUALIDADE_ATIVO=true equivale a DETECTOR_QUALIDADE=onnx (nome antigo do M3)."""
    escolha = _escolha(settings)
    if escolha == "nulo":
        return DetectorNulo()
    if escolha == "heuristica":
        return DetectorHeuristico()
    if escolha == "onnx":
        from inspecao.inference.qualidade_onnx import DetectorOnnx

        return DetectorOnnx(settings.caminho_absoluto(settings.modelo_qualidade_path))
    raise RuntimeError(f"DETECTOR_QUALIDADE={escolha!r}; use um de {list(DETECTORES)}")


class DetectorMalConfigurado(RuntimeError):
    """A configuracao do detector nao resolve para um detector com politica registrada
    (contrato do onnx ausente ou divergente, detector desconhecido). A API responde 503
    com o motivo (`main.py`); o worker nem sobe."""


def nome_do_detector(settings: Settings) -> str:
    """Nome do detector que `carregar_detector` daria, sem carregar o modelo.

    Para a API (metricas, auditoria), que precisa da politica em uso sem abrir o onnxruntime.
    No onnx, le a `versao` do `<versao>.json` ao lado do `.onnx` e confere o contrato contra
    a politica registrada (o sha256 do `.onnx` so o worker confere, ao carregar)."""
    escolha = _escolha(settings)
    if escolha == "nulo":
        return DetectorNulo.nome
    if escolha == "heuristica":
        return DetectorHeuristico.nome
    if escolha == "onnx":
        import json

        from inspecao.inference.politica import validar_contrato_onnx

        caminho_json = settings.caminho_absoluto(settings.modelo_qualidade_path).with_suffix(
            ".json"
        )
        if not caminho_json.is_file():
            raise RuntimeError(
                f"contrato do modelo de qualidade ausente: {caminho_json} "
                "(gere com `make m7-exportar`; MODELO_QUALIDADE_PATH aponta a versao em uso)"
            )
        try:
            contrato = json.loads(caminho_json.read_text(encoding="utf-8"))
        except ValueError as erro:
            raise RuntimeError(f"contrato {caminho_json.name} corrompido: {erro}") from erro
        if not isinstance(contrato, dict):
            raise RuntimeError(f"contrato {caminho_json.name} corrompido: nao e um objeto json")
        validar_contrato_onnx(contrato)
        # A mesma checagem do DetectorOnnx: o arquivo de cada versao se chama <versao>.onnx.
        if caminho_json.stem != contrato["versao"]:
            raise RuntimeError(
                f"{caminho_json.with_suffix('.onnx').name} contem {contrato['versao']}: o "
                "arquivo de cada versao se chama <versao>.onnx (ex.: qualidade@v1.onnx)"
            )
        return contrato["versao"]
    raise RuntimeError(f"DETECTOR_QUALIDADE={escolha!r}; use um de {list(DETECTORES)}")


def decodificar(dados: bytes) -> np.ndarray:
    """Bytes JPEG -> array BGR. Importa cv2 aqui para nao pagar o import na API."""
    import cv2

    arr = np.frombuffer(dados, dtype=np.uint8)
    imagem = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if imagem is None:
        raise ValueError("imagem invalida ou corrompida")
    return imagem

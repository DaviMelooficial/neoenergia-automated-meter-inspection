"""Contrato dos detectores de qualidade e a implementacao nula.

O detector so devolve scores. A decisao (rejeitar, seguir, humano) vive em
`inference.politica`, para que `recalcular_decisao` (M5) reaplique a politica
sem tocar em imagem.

| Implementacao       | Marco | Descricao                                          |
| DetectorNulo        | M3    | devolve {}, tudo vai para a fila humana            |
| DetectorHeuristico  | M5    | OpenCV, ver plano 04 e inference/heuristics.py     |
| DetectorOnnx        | M7    | modelo treinado via onnxruntime                    |
"""

from typing import Any, Protocol, runtime_checkable

import numpy as np

from inspecao.config import Settings


@runtime_checkable
class Detector(Protocol):
    """`avaliar` devolve o que vai para `resultados_inferencia.saida`: um score em
    [0, 1] por classe de qualidade (chaves de `politica.CLASSES`) e, opcionalmente,
    outras chaves de diagnostico, que a politica ignora."""

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


DETECTORES: dict[str, type[DetectorNulo] | type[DetectorHeuristico]] = {
    "nulo": DetectorNulo,
    "heuristica": DetectorHeuristico,
}


def carregar_detector(settings: Settings) -> Detector:
    """Escolhe o detector por configuracao. Chamado UMA vez por worker."""
    if settings.modelo_qualidade_ativo:
        # DetectorOnnx entra no marco M7. Ate la, ativar o modelo e erro de config.
        raise RuntimeError("MODELO_QUALIDADE_ATIVO=true, mas DetectorOnnx so existe a partir do M7")
    try:
        return DETECTORES[settings.detector_qualidade]()
    except KeyError:
        raise RuntimeError(
            f"DETECTOR_QUALIDADE={settings.detector_qualidade!r}; use um de {sorted(DETECTORES)}"
        ) from None


def decodificar(dados: bytes) -> np.ndarray:
    """Bytes JPEG -> array BGR. Importa cv2 aqui para nao pagar o import na API."""
    import cv2

    arr = np.frombuffer(dados, dtype=np.uint8)
    imagem = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if imagem is None:
        raise ValueError("imagem invalida ou corrompida")
    return imagem

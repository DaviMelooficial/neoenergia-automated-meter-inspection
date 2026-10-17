"""Politica de decisao a partir dos scores do detector. Versionada.

v0 (marco M3): nao existe sinal calibrado. Tudo vai para a fila humana.

v1 (marco M5): calibrada sobre `heuristica@v1`. So auto-rejeita; `limiar_aprova`
continua desativado ate haver rotulos suficientes para medir o erro de
auto-aprovacao (plano 04, secao 5.1: cerca de 600 auto-aprovadas rotuladas com
zero erro). A escolha do limiar esta em ml/reports/heuristicas.md.

v2 (marco M7): calibrada sobre `qualidade@v1` (modelo ONNX). Decide so pela chave
`ilegivel` (1 - P(legivel) calibrada); as 5 classes de defeito do modelo sao so
informativas (rotulo humano de defeito com kappa baixo). Limiar escolhido na validacao
por predicao seletiva com zero falso positivo: ml/reports/<rodada>/ do M7.

Toda versao fica registrada em POLITICAS: `recalcular_decisao` recebe a versao
pelo nome, e o historico de status guarda qual versao decidiu cada leitura.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# As 5 classes de qualidade (plano 04, secao 1.1). p_ruim e o maior score entre
# elas; outras chaves da saida do detector (ex.: `sinais`) nao entram na decisao.
CLASSES: tuple[str, ...] = (
    "fora_de_foco",
    "glare",
    "tampa_suja",
    "enquadramento_incorreto",
    "display_apagado",
)


class Decisao(StrEnum):
    REJEITAR = "rejeitar"  # -> rejeitada_qualidade
    SEGUIR = "seguir"  # -> validacao_leitura
    HUMANO = "humano"  # -> fila_humana


@dataclass(frozen=True, slots=True)
class Politica:
    versao: str
    # Detector cujos scores os limiares interpretam. None = qualquer um (so faz
    # sentido sem limiar). Limiar de um detector aplicado a outro e erro silencioso.
    modelo: str | None = None
    limiar_rejeita: float | None = None  # None = nunca auto-rejeita
    limiar_aprova: float | None = None  # None = nunca auto-aprova (padrao ate M8)
    # Chaves da saida do detector que entram em p_ruim (o maior score entre elas).
    chaves: tuple[str, ...] = CLASSES
    # Rodada de treino (ml/reports/<rodada>/) e sha256 do .onnx exportado que esta politica
    # calibrou. So para detector treinado: o DetectorOnnx recusa contrato de outra rodada ou
    # outro arquivo (outra semente da mesma rodada muda o sha).
    rodada: str | None = None
    onnx_sha256: str | None = None

    def decidir(self, saida: Mapping[str, Any]) -> tuple[Decisao, float | None]:
        """Devolve (decisao, p_ruim). p_ruim = maior score entre as `chaves` presentes."""
        valores = [float(saida[c]) for c in self.chaves if c in saida]
        if not valores:
            return Decisao.HUMANO, None
        p_ruim = max(valores)
        if self.limiar_rejeita is not None and p_ruim >= self.limiar_rejeita:
            return Decisao.REJEITAR, p_ruim
        if self.limiar_aprova is not None and p_ruim <= self.limiar_aprova:
            return Decisao.SEGUIR, p_ruim
        return Decisao.HUMANO, p_ruim

    def aceita(self, modelo: str) -> bool:
        return self.modelo is None or self.modelo == modelo


# Versao publicada e imutavel: o historico de status grava o nome da versao que
# decidiu. Limiar novo = Politica nova (proximo numero livre), nunca edicao destas.
POLITICA_V0 = Politica(versao="politica@v0")
POLITICA_V1 = Politica(versao="politica@v1", modelo="heuristica@v1", limiar_rejeita=0.85)
# Rodada 20261016-213656, semente 0: menor p_ilegivel rejeitado corretamente acima da foto
# lida mais alta da validacao (0,9613), 0,97527, arredondado para cima em 3 casas. Os mesmos
# valores estao em ml/artifacts/qualidade@v1.json (e na copia em ml/reports/<rodada>/).
POLITICA_V2 = Politica(
    versao="politica@v2",
    modelo="qualidade@v1",
    limiar_rejeita=0.976,
    chaves=("ilegivel",),
    rodada="20261016-213656",
    onnx_sha256="ab653b59e90e5e8e16637bc5ead99f1beac988df693291f724459b8e53304b72",
)

POLITICAS: dict[str, Politica] = {p.versao: p for p in (POLITICA_V0, POLITICA_V1, POLITICA_V2)}
POLITICA_ATUAL = POLITICA_V1
# Politica que o worker usa para cada detector. Unica fonte: o worker resolve por aqui, e
# detector fora da tabela e erro (cair em v0 em silencio mandaria toda rejeitada para a fila).
POLITICA_DO_DETECTOR: dict[str, Politica] = {
    "nulo@v0": POLITICA_V0,
    "heuristica@v1": POLITICA_V1,
    "qualidade@v1": POLITICA_V2,
}
TOLERANCIA_LIMIAR = 1e-9


def politica_para(modelo: str) -> Politica:
    try:
        return POLITICA_DO_DETECTOR[modelo]
    except KeyError:
        raise LookupError(
            f"detector {modelo!r} sem politica registrada em POLITICA_DO_DETECTOR"
        ) from None


def validar_contrato_onnx(contrato: Mapping[str, Any]) -> Politica:
    """Confere o json de um modelo exportado (`<versao>.json`) contra a politica registrada
    para a versao que ele declara. Devolve a politica; levanta RuntimeError na divergencia.

    Sem isso, exportar outra rodada (ou outra semente) com o mesmo nome faria o worker
    aplicar o limiar de um modelo a outro e gravar os dois sob o mesmo nome em
    `resultados_inferencia.modelo`. Rodada nova = versao nova (qualidade@v2) com a politica
    nova dela (politica@v3) registrada aqui, com rodada, limiar e sha256 do .onnx.
    """
    versao = contrato.get("versao")
    politica = POLITICA_DO_DETECTOR.get(versao)
    if politica is None or politica.rodada is None:
        raise RuntimeError(
            f"contrato do modelo declara versao {versao!r}, sem politica calibrada para modelo "
            "treinado em POLITICA_DO_DETECTOR"
        )
    limiar = contrato.get("limiar_rejeita")
    divergencias = [
        f"{campo}: contrato {contrato.get(campo)!r} != politica {esperado!r}"
        for campo, esperado in (
            ("rodada", politica.rodada),
            ("onnx_sha256", politica.onnx_sha256),
        )
        if contrato.get(campo) != esperado
    ]
    if not (
        isinstance(limiar, int | float)
        and politica.limiar_rejeita is not None
        and math.isclose(limiar, politica.limiar_rejeita, rel_tol=0, abs_tol=TOLERANCIA_LIMIAR)
    ):
        divergencias.append(
            f"limiar_rejeita: contrato {limiar!r} != politica {politica.limiar_rejeita!r}"
        )
    if divergencias:
        raise RuntimeError(
            f"contrato de {versao} nao e o modelo que {politica.versao} calibrou ("
            + "; ".join(divergencias)
            + "). Rodada nova exige versao nova do detector e politica nova."
        )
    return politica


def obter(versao: str) -> Politica:
    try:
        return POLITICAS[versao]
    except KeyError:
        raise LookupError(f"politica {versao!r} nao existe; versoes: {sorted(POLITICAS)}") from None

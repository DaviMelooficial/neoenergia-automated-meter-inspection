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
# lida mais alta da validacao (0,9613), 0,97527, arredondado para cima em 3 casas. O mesmo
# valor esta em qualidade.json.
POLITICA_V2 = Politica(
    versao="politica@v2",
    modelo="qualidade@v1",
    limiar_rejeita=0.976,
    chaves=("ilegivel",),
)

POLITICAS: dict[str, Politica] = {p.versao: p for p in (POLITICA_V0, POLITICA_V1, POLITICA_V2)}
POLITICA_ATUAL = POLITICA_V1
# Politica que o worker usa para cada detector. Detector sem politica calibrada cai na
# v0: tudo para humano.
POLITICA_DO_DETECTOR: dict[str, Politica] = {
    "nulo@v0": POLITICA_V0,
    "heuristica@v1": POLITICA_V1,
    "qualidade@v1": POLITICA_V2,
}


def politica_para(modelo: str) -> Politica:
    return POLITICA_DO_DETECTOR.get(modelo, POLITICA_V0)


def obter(versao: str) -> Politica:
    try:
        return POLITICAS[versao]
    except KeyError:
        raise LookupError(f"politica {versao!r} nao existe; versoes: {sorted(POLITICAS)}") from None

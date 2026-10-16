"""Politica de decisao a partir dos scores do detector. Versionada.

v0 (marco M3): nao existe sinal calibrado. Tudo vai para a fila humana.

v1 (marco M5): calibrada sobre `heuristica@v1`. So auto-rejeita; `limiar_aprova`
continua desativado ate haver rotulos suficientes para medir o erro de
auto-aprovacao (plano 04, secao 5.1: cerca de 600 auto-aprovadas rotuladas com
zero erro). A escolha do limiar esta em ml/reports/heuristicas.md.

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

    def decidir(self, saida: Mapping[str, Any]) -> tuple[Decisao, float | None]:
        """Devolve (decisao, p_ruim). p_ruim = maior score entre as classes de defeito."""
        valores = [float(saida[c]) for c in CLASSES if c in saida]
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


POLITICA_V0 = Politica(versao="politica@v0")
POLITICA_V1 = Politica(versao="politica@v1", modelo="heuristica@v1", limiar_rejeita=0.85)

POLITICAS: dict[str, Politica] = {p.versao: p for p in (POLITICA_V0, POLITICA_V1)}
POLITICA_ATUAL = POLITICA_V1


def obter(versao: str) -> Politica:
    try:
        return POLITICAS[versao]
    except KeyError:
        raise LookupError(f"politica {versao!r} nao existe; versoes: {sorted(POLITICAS)}") from None

"""Politica de decisao a partir dos scores do detector. Versionada.

v0 (marco M3): nao existe sinal calibrado. Qualquer saida vazia vai para a fila
humana. Os dois limiares ja estao na estrutura, mas `LIMIAR_APROVA` fica
desativado ate haver rotulos suficientes para medir o erro de auto-aprovacao
(plano 04, secao 5.1: cerca de 600 auto-aprovadas rotuladas com zero erro).

A calibracao real dos limiares e o marco M5 (T-M5-05).
"""

from dataclasses import dataclass
from enum import StrEnum

VERSAO = "politica@v0"


class Decisao(StrEnum):
    REJEITAR = "rejeitar"  # -> rejeitada_qualidade
    SEGUIR = "seguir"  # -> validacao_leitura
    HUMANO = "humano"  # -> fila_humana


@dataclass(frozen=True, slots=True)
class Politica:
    versao: str = VERSAO
    limiar_rejeita: float | None = None  # None = nunca auto-rejeita
    limiar_aprova: float | None = None  # None = nunca auto-aprova (padrao ate M8)

    def decidir(self, scores: dict[str, float]) -> tuple[Decisao, float | None]:
        """Devolve (decisao, p_ruim). p_ruim = maior score entre as classes de defeito."""
        if not scores:
            return Decisao.HUMANO, None
        p_ruim = max(scores.values())
        if self.limiar_rejeita is not None and p_ruim >= self.limiar_rejeita:
            return Decisao.REJEITAR, p_ruim
        if self.limiar_aprova is not None and p_ruim <= self.limiar_aprova:
            return Decisao.SEGUIR, p_ruim
        return Decisao.HUMANO, p_ruim


POLITICA_ATUAL = Politica()

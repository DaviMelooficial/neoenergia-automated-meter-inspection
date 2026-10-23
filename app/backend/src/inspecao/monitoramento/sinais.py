"""Definicoes compartilhadas pelas metricas do M8 (qualidade, drift, auto-aprovacao).

Tudo sai do banco, sem reabrir imagem: os scores que o worker grava em
`resultados_inferencia.saida` e os rotulos humanos de `rotulos`. A politica so e
lida do registro `inference.politica.POLITICAS`; nada aqui muda limiar.

Definicoes (as mesmas em todos os endpoints):
- p_ruim: o que a politica calcula sobre a saida (`Politica.decidir`). Na v1, o
  maior score entre as 5 classes de defeito.
- maquina diz "ilegivel": p_ruim >= limiar_rejeita da politica. Politica sem
  limiar de rejeicao (v0) nao tem veredito e fica fora de concordancia e drift.
- classe ativa: score da classe >= 0,5.
- zona cinzenta: 0,5 <= p_ruim < limiar_rejeita (o detector acha mais provavel
  ruim do que bom, mas nao a ponto de rejeitar).
- zona de aprovacao: p_ruim <= limiar_aprova da politica. Politica sem limiar_aprova
  nao tem zona de aprovacao, e o contador dos 600 nao se aplica a ela. A faixa
  p_ruim <= 0,05 (`LIMIAR_EXPLORATORIO`) so aparece como diagnostico do erro que essa
  faixa teria, medido no Lab 2: nao e candidato a limiar.
- veredito humano de producao, por foto: ilegivel se algum rotulo `producao` da
  foto tem legivel = false; legivel se todos os que tem legivel dizem true. Rotulo
  com legivel nulo (reprovada por `outro`) e rotulo de conta de teste nao contam.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inspecao.contas_teste import usuario_real
from inspecao.enums import RodadaRotulagem
from inspecao.inference import politica as politica_mod
from inspecao.inference.politica import Politica
from inspecao.models import Foto, ResultadoInferencia, Rotulo
from inspecao.rotulagem import servico
from inspecao.rotulagem.consolidacao import consolidar, kappa_cohen

LIMIAR_CLASSE_ATIVA = 0.5
INICIO_ZONA_CINZENTA = 0.5
LIMIAR_EXPLORATORIO = 0.05


def politica_ativa() -> Politica:
    """A politica que o worker aplica hoje: a do detector configurado (`politica_para`,
    a mesma resolucao do worker), e nao `POLITICA_ATUAL`."""
    from inspecao.config import get_settings
    from inspecao.inference.detector import DetectorMalConfigurado, nome_do_detector

    try:
        return politica_mod.politica_para(nome_do_detector(get_settings()))
    except (RuntimeError, LookupError) as erro:
        raise DetectorMalConfigurado(f"detector de qualidade mal configurado: {erro}") from erro


def obter_politica(versao: str | None) -> Politica:
    return politica_mod.obter(versao) if versao else politica_ativa()


def p_ruim(politica: Politica, saida: Mapping[str, Any]) -> float | None:
    return politica.decidir(saida)[1]


def maquina_ilegivel(politica: Politica, p: float | None) -> bool | None:
    if p is None or politica.limiar_rejeita is None:
        return None
    return p >= politica.limiar_rejeita


def na_zona_cinzenta(politica: Politica, p: float | None) -> bool | None:
    if p is None or politica.limiar_rejeita is None:
        return None
    return INICIO_ZONA_CINZENTA <= p < politica.limiar_rejeita


def classes_ativas(politica: Politica, saida: Mapping[str, Any]) -> dict[str, bool]:
    """Classe ativa por chave que a politica le (`Politica.chaves`): as 5 classes de defeito
    na v1, so `ilegivel` na v2 (as classes do modelo sao informativas e ficam fora)."""
    return {c: float(saida[c]) >= LIMIAR_CLASSE_ATIVA for c in politica.chaves if c in saida}


@dataclass(frozen=True, slots=True)
class Resultado:
    lote_id: int
    foto_id: int
    saida: dict[str, Any]


def ultimos_resultados(db: Session, modelo: str | None) -> list[Resultado]:
    """Resultado mais recente do detector `modelo` para cada foto principal.

    `saida - 'sinais'`: os sinais brutos nao entram em p_ruim e so pesariam na
    transferencia. Sem detector (politica v0) nao ha score: lista vazia.
    """
    if modelo is None:
        return []
    consulta = (
        select(Foto.lote_id, Foto.id, ResultadoInferencia.saida.op("-")("sinais"))
        .join(ResultadoInferencia, ResultadoInferencia.foto_id == Foto.id)
        .where(Foto.principal.is_(True), ResultadoInferencia.modelo == modelo)
        .distinct(Foto.id)
        .order_by(Foto.id, ResultadoInferencia.id.desc())
    )
    return [Resultado(lote, foto, saida) for lote, foto, saida in db.execute(consulta)]


def veredito_producao(db: Session) -> dict[int, bool]:
    """foto_id -> legivel segundo os rotulos `producao` (ver docstring do modulo)."""
    consulta = (
        select(Rotulo.foto_id, func.bool_and(Rotulo.legivel))
        .where(
            Rotulo.rodada == RodadaRotulagem.PRODUCAO.value,
            Rotulo.legivel.is_not(None),
            usuario_real(Rotulo.usuario_id),
        )
        .group_by(Rotulo.foto_id)
    )
    return dict(db.execute(consulta).tuples().all())


def veredito_lab02(db: Session) -> dict[int, bool]:
    """foto_id -> legivel pela maioria do Lab 2 (rodada fechada). Empate fica fora.

    As rodadas cegas (a, b, desempate) nao entram: o agregado delas e so de admin.
    """
    por_foto = servico.votos(db, [RodadaRotulagem.LAB02])
    return {
        c.foto: bool(c.valores["legivel"])
        for c in consolidar(por_foto)
        if c.valores["legivel"] is not None
    }


@dataclass(slots=True)
class Concordancia:
    """Matriz 2x2 maquina x humano sobre "ilegivel". Chaves: maquina_humano."""

    matriz: dict[str, int] = field(
        default_factory=lambda: {
            "rejeita_ilegivel": 0,
            "rejeita_legivel": 0,
            "passa_ilegivel": 0,
            "passa_legivel": 0,
        }
    )
    _maquina: list[int] = field(default_factory=list)
    _humano: list[int] = field(default_factory=list)

    def adicionar(self, maquina_ilegivel: bool, humano_legivel: bool) -> None:
        chave = ("rejeita" if maquina_ilegivel else "passa") + (
            "_legivel" if humano_legivel else "_ilegivel"
        )
        self.matriz[chave] += 1
        self._maquina.append(int(maquina_ilegivel))
        self._humano.append(int(not humano_legivel))

    def somar(self, outra: "Concordancia") -> None:
        for chave, n in outra.matriz.items():
            self.matriz[chave] += n
        self._maquina += outra._maquina
        self._humano += outra._humano

    @property
    def n(self) -> int:
        return len(self._maquina)

    @property
    def discordancia(self) -> float | None:
        if not self.n:
            return None
        return (self.matriz["rejeita_legivel"] + self.matriz["passa_ilegivel"]) / self.n

    @property
    def concordancia(self) -> float | None:
        d = self.discordancia
        return None if d is None else 1 - d

    @property
    def kappa(self) -> float | None:
        return kappa_cohen(self._maquina, self._humano)

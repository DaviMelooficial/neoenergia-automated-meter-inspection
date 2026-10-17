"""Drift por lote: os 5 sinais do plano 04, secao 7.1, contra a baseline.

Calculado so com o que ja esta no banco: os scores que o worker gravou em
`resultados_inferencia` (o resultado mais recente do detector da politica, por foto
principal) e os rotulos `producao`. Nenhuma imagem e reaberta. A baseline e o
conjunto dos lotes de `DRIFT_LOTES_BASELINE` (padrao: os 3 lotes de maio, treino e
validacao do split temporal), com as fracoes somadas sobre todas as fotos desses
lotes e nao a media das fracoes de cada lote.

Criterio de alerta (desvio = valor do lote - valor da baseline):

- distribuicao das 5 classes: fracao de fotos com score da classe >= 0,5. Alerta com
  |desvio| > 10 p.p. em qualquer classe.
- taxa de auto-rejeicao: fracao com p_ruim >= limiar_rejeita. Alerta com |desvio| >
  10 p.p. em 3 lotes seguidos (este e os 2 anteriores).
- confianca media: media de max(p_ruim, 1 - p_ruim). Alerta com desvio < -0,05 em 2
  lotes seguidos (queda sustentada).
- discordancia humano x modelo: fracao dos pares (veredito da maquina, rotulo
  `producao`) que discordam sobre "ilegivel". Alerta com desvio > +10 p.p., exigindo
  >= 30 pares no lote e na baseline.
- zona cinzenta (proxy de fabricante ou aparencia nova): fracao com 0,5 <= p_ruim <
  limiar_rejeita. Alerta com desvio > +10 p.p.

Desvios do plano, conscientes: (1) a referencia e a baseline fixa, e nao a media
movel dos ultimos 5 lotes: so existem 4 lotes, e a media movel sobre eles misturaria
o lote de teste de julho na propria referencia; (2) lote com menos de 100 fotos com
score nao dispara alerta (fica marcado `amostra_insuficiente`) e quebra a sequencia
de lotes seguidos. Os limiares de alerta estao nas constantes abaixo.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.inference.politica import CLASSES, Politica
from inspecao.models import Lote
from inspecao.monitoramento import sinais

MIN_FOTOS = 100
MIN_PARES_DISCORDANCIA = 30
DESVIO_CLASSE = 0.10
DESVIO_REJEICAO = 0.10
LOTES_SEGUIDOS_REJEICAO = 3
QUEDA_CONFIANCA = 0.05
LOTES_SEGUIDOS_CONFIANCA = 2
SUBIDA_DISCORDANCIA = 0.10
SUBIDA_ZONA_CINZENTA = 0.10

CRITERIO: dict[str, Any] = {
    "min_fotos": MIN_FOTOS,
    "min_pares_discordancia": MIN_PARES_DISCORDANCIA,
    "classe_ativa": sinais.LIMIAR_CLASSE_ATIVA,
    "inicio_zona_cinzenta": sinais.INICIO_ZONA_CINZENTA,
    "desvio_classe": DESVIO_CLASSE,
    "desvio_rejeicao": DESVIO_REJEICAO,
    "lotes_seguidos_rejeicao": LOTES_SEGUIDOS_REJEICAO,
    "queda_confianca": QUEDA_CONFIANCA,
    "lotes_seguidos_confianca": LOTES_SEGUIDOS_CONFIANCA,
    "subida_discordancia": SUBIDA_DISCORDANCIA,
    "subida_zona_cinzenta": SUBIDA_ZONA_CINZENTA,
}


@dataclass(slots=True)
class Acumulado:
    """Contagens de um lote (ou da baseline). As fracoes saem de `fracoes()`."""

    n: int = 0
    classes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    n_classe: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    rejeitadas: int = 0
    zona: int = 0
    soma_p: float = 0.0
    soma_confianca: float = 0.0
    pares: sinais.Concordancia = field(default_factory=sinais.Concordancia)

    def somar(self, outro: "Acumulado") -> None:
        self.n += outro.n
        for c in CLASSES:
            self.classes[c] += outro.classes[c]
            self.n_classe[c] += outro.n_classe[c]
        self.rejeitadas += outro.rejeitadas
        self.zona += outro.zona
        self.soma_p += outro.soma_p
        self.soma_confianca += outro.soma_confianca
        self.pares.somar(outro.pares)

    def fracoes(self, politica: Politica) -> dict[str, Any]:
        tem_limiar = politica.limiar_rejeita is not None

        def frac(x: int | float) -> float | None:
            return x / self.n if self.n else None

        return {
            "classes": {
                c: (self.classes[c] / self.n_classe[c] if self.n_classe[c] else None)
                for c in CLASSES
            },
            "taxa_auto_rejeicao": frac(self.rejeitadas) if tem_limiar else None,
            "zona_cinzenta": frac(self.zona) if tem_limiar else None,
            "p_ruim_medio": frac(self.soma_p),
            "confianca_media": frac(self.soma_confianca),
            "discordancia": self.pares.discordancia,
        }


def _acumular(
    politica: Politica, resultados: list[sinais.Resultado], humano: dict[int, bool]
) -> dict[int, Acumulado]:
    por_lote: dict[int, Acumulado] = defaultdict(Acumulado)
    for r in resultados:
        p = sinais.p_ruim(politica, r.saida)
        if p is None:
            continue
        a = por_lote[r.lote_id]
        a.n += 1
        a.soma_p += p
        a.soma_confianca += max(p, 1 - p)
        for c, ativa in sinais.classes_ativas(r.saida).items():
            a.n_classe[c] += 1
            a.classes[c] += int(ativa)
        ilegivel = sinais.maquina_ilegivel(politica, p)
        if ilegivel is not None:
            a.rejeitadas += int(ilegivel)
            a.zona += int(bool(sinais.na_zona_cinzenta(politica, p)))
            if r.foto_id in humano:
                a.pares.adicionar(ilegivel, humano[r.foto_id])
    return por_lote


def _sinal(valor: float | None, base: float | None, n: int) -> dict[str, Any]:
    desvio = None if valor is None or base is None else valor - base
    return {"valor": valor, "baseline": base, "desvio": desvio, "alerta": False, "n": n}


def calcular(db: Session, politica: Politica, lotes_baseline: list[str]) -> dict[str, Any]:
    lotes = db.scalars(select(Lote).order_by(Lote.data_extracao, Lote.nome)).all()
    resultados = sinais.ultimos_resultados(db, politica.modelo)
    acumulados = _acumular(politica, resultados, sinais.veredito_producao(db))

    na_baseline = [lote for lote in lotes if lote.nome in set(lotes_baseline)]
    base = Acumulado()
    for lote in na_baseline:
        base.somar(acumulados.get(lote.id, Acumulado()))
    fb = base.fracoes(politica) if base.n else None

    serie: list[dict[str, Any]] = []
    seguidos_rejeicao = 0
    seguidos_confianca = 0
    for lote in lotes:
        a = acumulados.get(lote.id, Acumulado())
        f = a.fracoes(politica)
        g = fb or {k: None for k in f} | {"classes": dict.fromkeys(CLASSES)}
        bloco = {
            "classes": {
                c: _sinal(f["classes"][c], g["classes"][c], a.n_classe[c]) for c in CLASSES
            },
            "taxa_auto_rejeicao": _sinal(f["taxa_auto_rejeicao"], g["taxa_auto_rejeicao"], a.n),
            "confianca_media": _sinal(f["confianca_media"], g["confianca_media"], a.n),
            "discordancia": _sinal(f["discordancia"], g["discordancia"], a.pares.n),
            "zona_cinzenta": _sinal(f["zona_cinzenta"], g["zona_cinzenta"], a.n),
        }
        alertas: list[str] = []
        suficiente = a.n >= MIN_FOTOS
        if not suficiente:
            seguidos_rejeicao = seguidos_confianca = 0
        else:
            for c, s in bloco["classes"].items():
                if s["desvio"] is not None and abs(s["desvio"]) > DESVIO_CLASSE:
                    s["alerta"] = True
                    alertas.append(f"classe {c}: {_pp(s['desvio'])} contra a baseline")
            s = bloco["taxa_auto_rejeicao"]
            fora = s["desvio"] is not None and abs(s["desvio"]) > DESVIO_REJEICAO
            seguidos_rejeicao = seguidos_rejeicao + 1 if fora else 0
            if seguidos_rejeicao >= LOTES_SEGUIDOS_REJEICAO:
                s["alerta"] = True
                alertas.append(
                    f"auto-rejeicao: {_pp(s['desvio'])} contra a baseline, "
                    f"{seguidos_rejeicao} lotes seguidos"
                )
            s = bloco["confianca_media"]
            caiu = s["desvio"] is not None and s["desvio"] < -QUEDA_CONFIANCA
            seguidos_confianca = seguidos_confianca + 1 if caiu else 0
            if seguidos_confianca >= LOTES_SEGUIDOS_CONFIANCA:
                s["alerta"] = True
                alertas.append(
                    f"confianca media: {s['desvio']:+.3f} contra a baseline, "
                    f"{seguidos_confianca} lotes seguidos"
                )
            s = bloco["discordancia"]
            com_pares = a.pares.n >= MIN_PARES_DISCORDANCIA and base.pares.n >= (
                MIN_PARES_DISCORDANCIA
            )
            if com_pares and s["desvio"] is not None and s["desvio"] > SUBIDA_DISCORDANCIA:
                s["alerta"] = True
                alertas.append(f"discordancia humano x modelo: {_pp(s['desvio'])}")
            s = bloco["zona_cinzenta"]
            if s["desvio"] is not None and s["desvio"] > SUBIDA_ZONA_CINZENTA:
                s["alerta"] = True
                alertas.append(f"zona cinzenta: {_pp(s['desvio'])} contra a baseline")
        serie.append(
            {
                "lote_id": lote.id,
                "lote": lote.nome,
                "data_extracao": lote.data_extracao,
                "na_baseline": lote in na_baseline,
                "n_fotos_com_score": a.n,
                "amostra_insuficiente": not suficiente,
                "p_ruim_medio": f["p_ruim_medio"],
                "sinais": bloco,
                "alertas": alertas,
            }
        )
    return {
        "politica": politica.versao,
        "detector": politica.modelo,
        "limiar_rejeita": politica.limiar_rejeita,
        "criterio": CRITERIO,
        "baseline": {
            "lotes": [lote.nome for lote in na_baseline],
            "n_fotos_com_score": base.n,
            "n_pares_discordancia": base.pares.n,
            "valores": fb,
        },
        "serie": serie,
    }


def _pp(desvio: float) -> str:
    return f"{100 * desvio:+.1f} p.p."

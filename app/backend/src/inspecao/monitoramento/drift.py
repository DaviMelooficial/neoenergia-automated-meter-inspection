"""Drift por lote: os 5 sinais do plano 04, secao 7.1, contra a baseline.

Calculado so com o que ja esta no banco: os scores que o worker gravou em
`resultados_inferencia` (o resultado mais recente do detector da politica, por foto
principal) e os rotulos `producao`. Nenhuma imagem e reaberta. A baseline e o
conjunto dos lotes de `DRIFT_LOTES_BASELINE` (padrao: os 3 lotes de maio, treino e
validacao do split temporal), com as fracoes somadas sobre todas as fotos desses
lotes e nao a media das fracoes de cada lote.

Criterio de alerta (desvio = valor do lote - valor da baseline):

- distribuicao das classes: fracao de fotos com score da classe >= 0,5, para cada chave
  que a politica le (`Politica.chaves`: as 5 classes de defeito na v1, so `ilegivel` na
  v2). Alerta com |desvio| > 10 p.p. em qualquer classe.
- taxa de auto-rejeicao: fracao com p_ruim >= limiar_rejeita. Alerta com |desvio| >
  10 p.p. em 3 lotes seguidos (este e os 2 anteriores).
- confianca media: media de max(p_ruim, 1 - p_ruim). Alerta com desvio < -0,05 em 2
  lotes seguidos (queda sustentada).
- discordancia humano x modelo: fracao dos pares (veredito da maquina, rotulo
  `producao`) que discordam sobre "ilegivel". Alerta com desvio > +10 p.p., exigindo
  >= 30 pares no lote e na baseline.
- zona cinzenta (proxy de fabricante ou aparencia nova): fracao com 0,5 <= p_ruim <
  limiar_rejeita. Alerta com desvio > +10 p.p.

Complemento, carta p (para as fracoes: classes, auto-rejeicao, zona cinzenta):
z = (p_lote - p_base) / sqrt(phi * p_base * (1 - p_base) * (1/n_lote + 1/n_base)). O termo
1/n_base conta a incerteza da propria baseline; para um lote que esta na baseline os dois
nao sao independentes, e o z dele so serve de referencia. Alerta com |z| > 3. Motivo: o
limiar absoluto de 10 p.p. e cego para taxas de 1% a 5% (a auto-rejeicao fica em 0,8%,
com erro-padrao de ~0,19 p.p. por lote contra a baseline, ja com o termo 1/n_base).

phi (sobredispersao) = qui-quadrado de Pearson / (k - 1) entre os k lotes da baseline,
minimo 1, e SO e aplicado com k >= 5 lotes (`MIN_LOTES_PHI`); abaixo disso a carta usa
phi = 1 e publica o estimado a parte. Com os 3 lotes de maio o phi da auto-rejeicao sai
4,95 (qui-quadrado ~9,9 com 2 g.l., p ~0,007), mas com 2 g.l. o intervalo de 95% dele vai
de ~1,3 a ~195: aplica-lo deixaria o limite de alerta oscilando com cada lote novo (~2,03%
contra ~1,33% com phi = 1, na auto-rejeicao, |z| = 3 com o termo 1/n_base). Com phi = 1
a carta pode alarmar pela variacao normal entre lotes, que os lotes de maio mostram
existir; o alerta da carta e um convite a olhar, nao um veredito.

Pontos cegos declarados: `glare` tem p_base = 0 (a heuristica quase nunca da score >= 0,5
para reflexo), e a carta nao calcula z com p_base 0; `fora_de_foco` e
`enquadramento_incorreto` sao o mesmo sinal na heuristica (ambos limitados pelo score de
foco), entao um alerta num aparece no outro e conta uma vez so como evidencia.

Desvios do plano, conscientes: (1) a referencia e a baseline fixa, e nao a media
movel dos ultimos 5 lotes: so existem 4 lotes, e a media movel sobre eles misturaria
o lote de teste de julho na propria referencia; (2) lote com menos de 100 fotos com
score nao dispara alerta (fica marcado `amostra_insuficiente`) e quebra a sequencia
de lotes seguidos; (3) "confianca media" foi definida como a media de
max(p_ruim, 1 - p_ruim), e "queda sustentada" como cair mais de 0,05 em 2 lotes seguidos;
(4) a baseline contem os proprios lotes de maio, entao o desvio deles e contra um
agregado que os inclui. A baseline troca junto com o detector (`lotes_baseline`): o
modelo do M7 foi treinado e calibrado em maio, e a confianca dele la e inflada pelo
treino; para detector treinado a baseline sao os lotes de teste dele
(`qualidade.LOTES_DE_TESTE`, o 03/07 para a `qualidade@v1`). Os limiares de alerta estao
nas constantes abaixo.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.inference.politica import Politica
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
Z_CARTA_P = 3.0
MIN_LOTES_PHI = 5

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
    "z_carta_p": Z_CARTA_P,
    "min_lotes_phi": MIN_LOTES_PHI,
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
        for c in outro.n_classe:
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
                for c in politica.chaves
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
        for c, ativa in sinais.classes_ativas(politica, r.saida).items():
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
    return {"valor": valor, "baseline": base, "desvio": desvio, "alerta": False, "n": n, "z": None}


def _sobredispersao(contagens: list[tuple[int, int]]) -> float:
    """phi = qui-quadrado de Pearson / (k - 1) entre os lotes da baseline, minimo 1."""
    validos = [(k, n) for k, n in contagens if n]
    total_n = sum(n for _, n in validos)
    if len(validos) < 2 or not total_n:
        return 1.0
    p = sum(k for k, _ in validos) / total_n
    if p in (0.0, 1.0):
        return 1.0
    chi2 = sum((k - n * p) ** 2 / (n * p * (1 - p)) for k, n in validos)
    return max(1.0, chi2 / (len(validos) - 1))


def _z(k: int, n: int, p_base: float | None, n_base: int, phi: float) -> float | None:
    if not n or not n_base or p_base is None or p_base in (0.0, 1.0):
        return None
    return (k / n - p_base) / math.sqrt(phi * p_base * (1 - p_base) * (1 / n + 1 / n_base))


# fracao -> (contagem, denominador) num Acumulado; a carta p vale para estas
def _fracoes_contaveis(politica: Politica) -> dict[str, Any]:
    contaveis: dict[str, Any] = {
        "taxa_auto_rejeicao": lambda a: (a.rejeitadas, a.n),
        "zona_cinzenta": lambda a: (a.zona, a.n),
    }
    for c in politica.chaves:
        contaveis[f"classe:{c}"] = lambda a, c=c: (a.classes[c], a.n_classe[c])
    return contaveis


def lotes_baseline(politica: Politica, padrao: list[str]) -> list[str]:
    """Baseline do drift para o detector da politica. Heuristica e nulo (sem treino):
    `padrao` (`DRIFT_LOTES_BASELINE`, os 3 lotes de maio). Detector treinado: os lotes de
    teste dele, os unicos fora do treino e da calibracao; sem lotes de teste declarados,
    nenhuma baseline (em vez de uma contaminada pelo treino)."""
    from inspecao.monitoramento import qualidade

    if politica.modelo is None or politica.modelo in qualidade.DETECTORES_SEM_TREINO:
        return padrao
    return list(qualidade.LOTES_DE_TESTE.get(politica.modelo, ()))


def calcular(db: Session, politica: Politica, lotes_baseline: list[str]) -> dict[str, Any]:
    lotes = db.scalars(select(Lote).order_by(Lote.data_extracao, Lote.nome)).all()
    resultados = sinais.ultimos_resultados(db, politica.modelo)
    acumulados = _acumular(politica, resultados, sinais.veredito_producao(db))

    na_baseline = [lote for lote in lotes if lote.nome in set(lotes_baseline)]
    base = Acumulado()
    for lote in na_baseline:
        base.somar(acumulados.get(lote.id, Acumulado()))
    fb = base.fracoes(politica) if base.n else None
    tem_limiar = politica.limiar_rejeita is not None
    contaveis = {
        nome: f
        for nome, f in _fracoes_contaveis(politica).items()
        if tem_limiar or nome.startswith("classe:")
    }
    phi_estimado = {
        nome: _sobredispersao([f(acumulados.get(lote.id, Acumulado())) for lote in na_baseline])
        for nome, f in contaveis.items()
    }
    usa_phi = len(na_baseline) >= MIN_LOTES_PHI
    phi = {nome: (v if usa_phi else 1.0) for nome, v in phi_estimado.items()}

    serie: list[dict[str, Any]] = []
    seguidos_rejeicao = 0
    seguidos_confianca = 0
    for lote in lotes:
        a = acumulados.get(lote.id, Acumulado())
        f = a.fracoes(politica)
        g = fb or {k: None for k in f} | {"classes": dict.fromkeys(politica.chaves)}
        bloco = {
            "classes": {
                c: _sinal(f["classes"][c], g["classes"][c], a.n_classe[c]) for c in politica.chaves
            },
            "taxa_auto_rejeicao": _sinal(f["taxa_auto_rejeicao"], g["taxa_auto_rejeicao"], a.n),
            "confianca_media": _sinal(f["confianca_media"], g["confianca_media"], a.n),
            "discordancia": _sinal(f["discordancia"], g["discordancia"], a.pares.n),
            "zona_cinzenta": _sinal(f["zona_cinzenta"], g["zona_cinzenta"], a.n),
        }
        alertas: list[str] = []
        suficiente = a.n >= MIN_FOTOS
        for nome, f_contagem in contaveis.items():
            s = (
                bloco["classes"][nome.removeprefix("classe:")]
                if nome.startswith("classe:")
                else bloco[nome]
            )
            k, n = f_contagem(a)
            n_base = f_contagem(base)[1]
            s["z"] = _z(k, n, s["baseline"], n_base, phi[nome]) if fb else None
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
            for nome in contaveis:
                s = (
                    bloco["classes"][nome.removeprefix("classe:")]
                    if nome.startswith("classe:")
                    else bloco[nome]
                )
                if s["z"] is not None and abs(s["z"]) > Z_CARTA_P:
                    s["alerta"] = True
                    alertas.append(f"carta p, {nome.replace(':', ' ')}: z = {s['z']:+.1f}")
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
            "sobredispersao": phi,
            "sobredispersao_estimada": phi_estimado,
        },
        "serie": serie,
    }


def _pp(desvio: float) -> str:
    return f"{100 * desvio:+.1f} p.p."

"""Estado da auto-aprovacao (T-M8-05): desligada por padrao, com o motivo estatistico.

Plano 04, secao 5.1: zero erro em n amostras sustenta, com 95% de confianca, erro
abaixo de ~3/n (regra dos tres). Afirmar erro de auto-aprovacao abaixo de 0,5%
exige 600 amostras rotuladas na zona de aprovacao, com zero erro.

O que conta no contador (precisamente): fotos principais distintas que
1. tem resultado do detector da politica (o mais recente);
2. cairiam na zona de aprovacao: p_ruim <= limiar_aprova DA POLITICA;
3. tem veredito humano de producao (rotulo `producao` com legivel nao nulo, de conta
   real, vindo da decisao de auditor na fila ou na auditoria amostral).
Erro = uma dessas fotos com veredito humano "ilegivel". Depois de um erro, o contador
fica em 0 para aquele par (detector, limiar) ate surgir um limiar ou um detector novo:
a meta e zero erro, e um erro observado nao "sai" com mais amostras. So rotulos de
producao contam: os do Lab 2 e das rodadas cegas serviram para calibrar (M5) e treinar
(M7), e nao sao evidencia fora da amostra.

Politica sem `limiar_aprova` nao tem zona de aprovacao: o contador nao se aplica.
Para a heuristica isso nao e so falta de limiar: no Lab 2 a faixa p_ruim <= 0,05 tem
cerca de um terco de fotos ilegiveis (`diagnostico`), entao nenhum limiar baixo da
heuristica aprovaria com erro pequeno. O contador e para um detector com limiar_aprova
proprio, calibrado (M7).

O contador mede, nao decide: ligar a auto-aprovacao e trocar a politica por uma
versao nova com `limiar_aprova`, decisao humana fora deste modulo.
"""

import math
from typing import Any

from sqlalchemy.orm import Session

from inspecao.inference.politica import Politica
from inspecao.monitoramento import sinais

ALVO_ERRO = 0.005
NECESSARIAS = 600  # 3 / 0,005: regra dos tres com zero erro
CONFIANCA = 0.95


def _log_binom_cdf(k: int, n: int, p: float) -> float:
    """log P(X <= k), X ~ Binomial(n, p)."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 0.0 if k >= n else -math.inf
    termos = [
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * math.log(p)
        + (n - i) * math.log1p(-p)
        for i in range(k + 1)
    ]
    m = max(termos)
    return m + math.log(sum(math.exp(t - m) for t in termos))


def limite_superior(erros: int, n: int, confianca: float = CONFIANCA) -> float | None:
    """Limite superior unilateral exato (Clopper-Pearson) da taxa de erro.

    Com zero erro e 1 - (1 - confianca)^(1/n), que e ~3/n para 95% (regra dos tres).
    """
    if n == 0:
        return None
    if erros >= n:
        return 1.0
    alvo = math.log(1 - confianca)
    lo, hi = erros / n, 1.0
    for _ in range(100):
        meio = (lo + hi) / 2
        if _log_binom_cdf(erros, n, meio) > alvo:
            lo = meio
        else:
            hi = meio
    return hi


def limite_inferior(erros: int, n: int, confianca: float = CONFIANCA) -> float | None:
    """Limite inferior unilateral exato (Clopper-Pearson): a taxa e ao menos isto."""
    if n == 0:
        return None
    if erros == 0:
        return 0.0
    alvo = math.log(1 - confianca)
    lo, hi = 0.0, erros / n
    for _ in range(100):
        meio = (lo + hi) / 2
        # P(X >= erros) = 1 - P(X <= erros - 1)
        cauda = 1 - math.exp(_log_binom_cdf(erros - 1, n, meio))
        if cauda < math.exp(alvo):
            lo = meio
        else:
            hi = meio
    return lo


def _diagnostico(db: Session, politica: Politica) -> dict[str, Any] | None:
    """Erro que a faixa p_ruim <= 0,05 teria, medido no Lab 2 (dentro da amostra)."""
    if politica.modelo is None:
        return None
    lab02 = sinais.veredito_lab02(db)
    n = ilegiveis = 0
    for r in sinais.ultimos_resultados(db, politica.modelo):
        p = sinais.p_ruim(politica, r.saida)
        if p is None or p > sinais.LIMIAR_EXPLORATORIO or r.foto_id not in lab02:
            continue
        n += 1
        ilegiveis += int(not lab02[r.foto_id])
    return {
        "limiar": sinais.LIMIAR_EXPLORATORIO,
        "fonte": "lab02",
        "n": n,
        "ilegiveis": ilegiveis,
        "taxa_erro": ilegiveis / n if n else None,
        "limite_inferior_erro": limite_inferior(ilegiveis, n),
    }


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%".replace(".", ",")


def estado(db: Session, politica: Politica) -> dict[str, Any]:
    aplicavel = politica.limiar_aprova is not None
    na_zona = rotuladas = erros = 0
    if aplicavel:
        humano = sinais.veredito_producao(db)
        for r in sinais.ultimos_resultados(db, politica.modelo):
            p = sinais.p_ruim(politica, r.saida)
            if p is None or p > politica.limiar_aprova:
                continue
            na_zona += 1
            if r.foto_id in humano:
                rotuladas += 1
                erros += int(not humano[r.foto_id])
    diagnostico = _diagnostico(db, politica)
    alvo = (
        f"Para afirmar erro de auto-aprovacao abaixo de {_pct(ALVO_ERRO)} com "
        f"{100 * CONFIANCA:.0f}% de confianca (regra dos tres, plano 04 secao 5.1) sao "
        f"necessarias {NECESSARIAS} leituras da zona de aprovacao rotuladas por auditor, com "
        "zero erro."
    )
    if aplicavel:
        motivo = (
            f"{politica.versao} define limiar_aprova = {politica.limiar_aprova}. {alvo} "
            f"Acumuladas: {rotuladas}, com {erros} erro(s)."
        )
        if erros:
            motivo += (
                " Com erro observado, o contador fica em 0 para este detector e limiar ate "
                "surgir um limiar ou detector novo."
            )
    elif politica.modelo is None:
        motivo = (
            f"Auto-aprovacao desligada: {politica.versao} nao interpreta score de detector "
            "nenhum (tudo vai para a fila humana)."
        )
    else:
        motivo = f"Auto-aprovacao desligada: {politica.versao} nao define limiar_aprova"
        if diagnostico and diagnostico["n"]:
            motivo += (
                f", e o detector {politica.modelo} nao tem zona de aprovacao com erro baixo: "
                f"no Lab 2, {diagnostico['ilegiveis']} das {diagnostico['n']} fotos com "
                f"p_ruim <= {f'{sinais.LIMIAR_EXPLORATORIO:.2f}'.replace('.', ',')} sao "
                f"ilegiveis ({_pct(diagnostico['taxa_erro'])})"
            )
        motivo += (
            f". {alvo} Esse contador so faz sentido para um detector com limiar_aprova "
            "proprio, calibrado (M7); para esta politica ele nao se aplica."
        )
    return {
        "politica": politica.versao,
        "detector": politica.modelo,
        "limiar_rejeita": politica.limiar_rejeita,
        "limiar_aprova": politica.limiar_aprova,
        "ativa": aplicavel,
        "contador_aplicavel": aplicavel,
        "motivo": motivo,
        "alvo_erro": ALVO_ERRO,
        "confianca": CONFIANCA,
        "necessarias": NECESSARIAS,
        "fotos_na_zona": na_zona if aplicavel else None,
        "rotuladas_na_zona": rotuladas,
        "erros": erros,
        "acumuladas_sem_erro": rotuladas if erros == 0 else 0,
        "limite_superior_erro": limite_superior(erros, rotuladas),
        "meta_atingida": aplicavel and erros == 0 and rotuladas >= NECESSARIAS,
        "diagnostico": diagnostico,
    }

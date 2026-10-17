"""Estado da auto-aprovacao (T-M8-05): desligada por padrao, com o motivo estatistico.

Plano 04, secao 5.1: zero erro em n amostras sustenta, com 95% de confianca, erro
abaixo de ~3/n (regra dos tres). Afirmar erro de auto-aprovacao abaixo de 0,5%
exige 600 amostras rotuladas na zona de aprovacao, com zero erro.

O que conta no contador (precisamente): fotos principais distintas que
1. tem resultado do detector da politica (o mais recente);
2. cairiam na zona de aprovacao: p_ruim <= limiar_aprova da politica, ou o
   candidato do M8 (0,05) enquanto a politica nao define um;
3. tem veredito humano de producao (rotulo `producao` com legivel nao nulo, vindo da
   decisao de auditor na fila ou na auditoria amostral).
Erro = uma dessas fotos com veredito humano "ilegivel". So rotulos de producao
contam: os do Lab 2 e das rodadas cegas serviram para treinar e calibrar, e nao sao
evidencia fora da amostra.

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


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%".replace(".", ",")


def estado(db: Session, politica: Politica) -> dict[str, Any]:
    limiar, origem = sinais.limiar_aprova(politica)
    humano = sinais.veredito_producao(db)
    na_zona = 0
    rotuladas = 0
    erros = 0
    for r in sinais.ultimos_resultados(db, politica.modelo):
        p = sinais.p_ruim(politica, r.saida)
        if p is None or p > limiar:
            continue
        na_zona += 1
        if r.foto_id in humano:
            rotuladas += 1
            erros += int(not humano[r.foto_id])

    ativa = politica.limiar_aprova is not None
    teto = limite_superior(erros, rotuladas)
    if ativa:
        motivo = f"{politica.versao} define limiar_aprova = {politica.limiar_aprova}."
    elif politica.modelo is None:
        motivo = (
            f"{politica.versao} nao interpreta score de detector nenhum: tudo vai para a "
            "fila humana e nao ha zona de aprovacao para medir."
        )
    else:
        motivo = (
            f"Auto-aprovacao desligada: {politica.versao} nao define limiar_aprova. "
            f"Para afirmar erro de auto-aprovacao abaixo de {_pct(ALVO_ERRO)} com "
            f"{100 * CONFIANCA:.0f}% de confianca (regra dos tres, plano 04 secao 5.1) sao "
            f"necessarias {NECESSARIAS} leituras da zona de aprovacao rotuladas por auditor, "
            f"com zero erro. Acumuladas: {rotuladas}, com {erros} erro(s)."
        )
    return {
        "politica": politica.versao,
        "detector": politica.modelo,
        "limiar_rejeita": politica.limiar_rejeita,
        "limiar_aprova": politica.limiar_aprova,
        "ativa": ativa,
        "motivo": motivo,
        "limiar_contagem": limiar,
        "origem_limiar_contagem": origem,
        "alvo_erro": ALVO_ERRO,
        "confianca": CONFIANCA,
        "necessarias": NECESSARIAS,
        "fotos_na_zona": na_zona,
        "rotuladas_na_zona": rotuladas,
        "erros": erros,
        "acumuladas_sem_erro": rotuladas if erros == 0 else 0,
        "limite_superior_erro": teto,
        "meta_atingida": erros == 0 and rotuladas >= NECESSARIAS,
    }

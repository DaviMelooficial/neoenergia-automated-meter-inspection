"""Maquina de estados: tabela de transicoes e a garantia de caminho unico."""

import re
from pathlib import Path

import pytest

from inspecao.enums import StatusLeitura
from inspecao.estados import (
    ESTADO_INICIAL,
    ESTADOS_FINAIS,
    TRANSICOES,
    TransicaoInvalida,
    validar_transicao,
)

RAIZ_SRC = Path(__file__).resolve().parents[1] / "src" / "inspecao"


def test_todo_estado_tem_entrada_na_tabela():
    assert set(TRANSICOES) == set(StatusLeitura)


def test_estado_inicial_e_recebida_e_nao_e_final():
    assert ESTADO_INICIAL is StatusLeitura.RECEBIDA
    assert ESTADO_INICIAL not in ESTADOS_FINAIS


def test_estados_finais_sao_exatamente_os_seis_terminais():
    assert ESTADOS_FINAIS == {
        StatusLeitura.REJEITADA_QUALIDADE,
        StatusLeitura.AUTO_APROVADA,
        StatusLeitura.AUTO_REJEITADA,
        StatusLeitura.APROVADA_HUMANO,
        StatusLeitura.REPROVADA_HUMANO,
        StatusLeitura.SEM_FOTO,
    }


@pytest.mark.parametrize(
    ("de", "para"),
    [
        (StatusLeitura.RECEBIDA, StatusLeitura.SEM_FOTO),
        (StatusLeitura.RECEBIDA, StatusLeitura.TRIAGEM_QUALIDADE),
        (StatusLeitura.TRIAGEM_QUALIDADE, StatusLeitura.VALIDACAO_LEITURA),
        (StatusLeitura.VALIDACAO_LEITURA, StatusLeitura.FILA_HUMANA),
        (StatusLeitura.FILA_HUMANA, StatusLeitura.APROVADA_HUMANO),
    ],
)
def test_transicoes_permitidas(de, para):
    validar_transicao(de, para)


@pytest.mark.parametrize(
    ("de", "para"),
    [
        (StatusLeitura.RECEBIDA, StatusLeitura.AUTO_APROVADA),  # pula a triagem
        (StatusLeitura.RECEBIDA, StatusLeitura.RECEBIDA),  # laco
        (StatusLeitura.SEM_FOTO, StatusLeitura.TRIAGEM_QUALIDADE),  # sai de final
        (StatusLeitura.APROVADA_HUMANO, StatusLeitura.REPROVADA_HUMANO),  # muda veredito
        (StatusLeitura.FILA_HUMANA, StatusLeitura.AUTO_APROVADA),  # humano nao auto-decide
    ],
)
def test_transicoes_proibidas(de, para):
    with pytest.raises(TransicaoInvalida):
        validar_transicao(de, para)


def test_aceita_status_como_string():
    validar_transicao("recebida", "sem_foto")
    with pytest.raises(ValueError):
        validar_transicao("recebida", "inexistente")


def test_status_atual_so_e_escrito_por_estados_py():
    """Varredura do codigo-fonte (T-M3-08, antecipado para o M2).

    Qualquer escrita em `status_atual` fora de `estados.py` quebra este teste.
    Unica excecao: a insercao inicial na ingestao, que precisa citar
    ESTADO_INICIAL na mesma linha.
    """
    padroes = [
        re.compile(r"\.status_atual\s*=[^=]"),
        re.compile(r"status_atual\s*=[^=]"),  # values(status_atual=...)
        re.compile(r"[\"']status_atual[\"']\s*:"),  # bulk mappings
        re.compile(r"setattr\(.*status_atual"),
    ]
    excecao_batch = '"status_atual": ESTADO_INICIAL.value,'
    violacoes: list[str] = []
    for arquivo in RAIZ_SRC.rglob("*.py"):
        if arquivo.name == "estados.py":
            continue
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(linha) for p in padroes):
                if arquivo.name == "batch.py" and linha.strip().startswith(excecao_batch):
                    continue
                if "mapped_column" in linha:  # a declaracao da coluna em models.py
                    continue
                violacoes.append(f"{arquivo.relative_to(RAIZ_SRC)}:{numero}: {linha.strip()}")
    assert not violacoes, "status_atual escrito fora de estados.py:\n" + "\n".join(violacoes)

"""Enumeracoes do dominio."""

from enum import StrEnum


class StatusLeitura(StrEnum):
    """Estados de uma leitura. A maquina de transicoes entra no marco M2."""

    RECEBIDA = "recebida"
    TRIAGEM_QUALIDADE = "triagem_qualidade"
    REJEITADA_QUALIDADE = "rejeitada_qualidade"
    VALIDACAO_LEITURA = "validacao_leitura"
    AUTO_APROVADA = "auto_aprovada"
    AUTO_REJEITADA = "auto_rejeitada"
    FILA_HUMANA = "fila_humana"
    APROVADA_HUMANO = "aprovada_humano"
    REPROVADA_HUMANO = "reprovada_humano"
    SEM_FOTO = "sem_foto"


class TipoInconsistencia(StrEnum):
    """Tipos de registro em quarentena. Usados a partir do marco M2."""

    NOTA_EXIGE_SEM_FOTO = "nota_exige_sem_foto"
    FOTO_SEM_EXIGENCIA = "foto_sem_exigencia"
    CODIGO_NAO_MAPEADO = "codigo_nao_mapeado"
    FOTO_ORFA = "foto_orfa"
    MEDIDOR_MULTIPLO = "medidor_multiplo"
    POSICAO_INVALIDA = "posicao_invalida"
    POSICAO_AUSENTE = "posicao_ausente"
    FOTO_REFERENCIADA_AUSENTE = "foto_referenciada_ausente"


class Severidade(StrEnum):
    BLOQUEANTE = "bloqueante"
    INFORMATIVO = "informativo"


class Ator(StrEnum):
    """Quem provocou uma transicao de estado. Modelos e usuarios usam prefixo:
    `modelo:qualidade@v1`, `usuario:<id>`."""

    INGESTAO = "ingestao"
    HEURISTICA = "heuristica"
    SISTEMA = "sistema"

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


class Papel(StrEnum):
    """Hierarquico: ADMIN > SUPERVISOR > AUDITOR. Comparar por ORDEM_PAPEL."""

    AUDITOR = "auditor"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


ORDEM_PAPEL: dict[Papel, int] = {Papel.AUDITOR: 1, Papel.SUPERVISOR: 2, Papel.ADMIN: 3}


class EventoAcesso(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FALHO = "login_falho"
    LOGIN_BLOQUEADO = "login_bloqueado"
    REFRESH = "refresh"
    REFRESH_REUSO = "refresh_reuso"
    SENHA_ALTERADA = "senha_alterada"


class StatusJob(StrEnum):
    PENDENTE = "pendente"
    PROCESSANDO = "processando"
    CONCLUIDO = "concluido"
    ERRO = "erro"


class TipoJob(StrEnum):
    TRIAGEM_QUALIDADE = "triagem_qualidade"
    VALIDACAO_LEITURA = "validacao_leitura"
    REPROCESSAR_LOTE = "reprocessar_lote"
    INGERIR_LOTE = "ingerir_lote"
    # recalcular_decisao entra no marco M5 (T-M5-06), junto com a politica calibrada.


class DecisaoHumana(StrEnum):
    APROVADA = "aprovada"
    REPROVADA = "reprovada"


class MotivoReprovacao(StrEnum):
    """Lista fechada: as 5 classes de qualidade mais `outro`. Vira rotulo de treino."""

    FORA_DE_FOCO = "fora_de_foco"
    GLARE = "glare"
    TAMPA_SUJA = "tampa_suja"
    ENQUADRAMENTO_INCORRETO = "enquadramento_incorreto"
    DISPLAY_APAGADO = "display_apagado"
    OUTRO = "outro"

"""Modelos ORM do dominio.

Desvio consciente em relacao ao plano (doc 02, secao 4.1): em vez de
`leituras.foto_principal_id` apontando para `fotos`, a relacao vive apenas em
`fotos.leitura_id` mais o booleano `fotos.principal`. As duas modelagens
expressam a mesma coisa, mas a do plano cria uma chave estrangeira circular
entre as duas tabelas, que o Alembic so resolve com `use_alter`. Esta versao
evita a circularidade sem perder informacao:

    foto referenciada no CSV -> leitura_id preenchido, principal = True
    foto orfa (866 casos)    -> leitura_id nulo,       principal = False
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from inspecao.db import Base
from inspecao.enums import Cena, DecisaoHumana, MotivoReprovacao, RodadaRotulagem, StatusLeitura


class Lote(Base):
    __tablename__ = "lotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    data_extracao: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    hora_extracao: Mapped[str | None] = mapped_column(String(8), nullable=True)
    csv_nome: Mapped[str] = mapped_column(String(160))
    checksum_csv: Mapped[str] = mapped_column(String(64), index=True)
    total_linhas: Mapped[int] = mapped_column(Integer, default=0)
    total_fotos_disco: Mapped[int] = mapped_column(Integer, default=0)
    ingerido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    leituras: Mapped[list["Leitura"]] = relationship(
        back_populates="lote", cascade="all, delete-orphan", passive_deletes=True
    )
    fotos: Mapped[list["Foto"]] = relationship(
        back_populates="lote", cascade="all, delete-orphan", passive_deletes=True
    )


class NotaOcorrencia(Base):
    __tablename__ = "notas_ocorrencia"

    codigo: Mapped[str] = mapped_column(String(10), primary_key=True)
    descricao: Mapped[str] = mapped_column(Text)
    exige_foto: Mapped[bool] = mapped_column(Boolean)
    cena_esperada: Mapped[str | None] = mapped_column(String(60), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Leitura(Base):
    __tablename__ = "leituras"
    __table_args__ = (
        UniqueConstraint("lote_id", "linha_csv", name="uq_leitura_lote_linha"),
        Index("ix_leituras_reserva", "reservado_por", "reservado_ate"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes.id", ondelete="CASCADE"), index=True)
    linha_csv: Mapped[int] = mapped_column(Integer)
    posicao_lida: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Sem chave estrangeira: codigos nao mapeados (V100) precisam entrar mesmo
    # sem existir em notas_ocorrencia. A validacao acontece na quarentena (M2).
    nota_codigo: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    status_atual: Mapped[str] = mapped_column(
        String(30), default=StatusLeitura.RECEBIDA, index=True
    )
    # Reserva de item da fila humana: evita dois auditores na mesma foto.
    reservado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    reservado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lote: Mapped["Lote"] = relationship(back_populates="leituras")
    medidores: Mapped[list["LeituraMedidor"]] = relationship(
        back_populates="leitura", cascade="all, delete-orphan", passive_deletes=True
    )
    fotos: Mapped[list["Foto"]] = relationship(back_populates="leitura")
    historico: Mapped[list["HistoricoStatus"]] = relationship(
        back_populates="leitura",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="HistoricoStatus.id",
    )
    revisoes: Mapped[list["RevisaoHumana"]] = relationship(
        back_populates="leitura",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RevisaoHumana.id",
    )


class LeituraMedidor(Base):
    """Um medidor por linha. A coluna de origem traz ate 8 separados por barra."""

    __tablename__ = "leitura_medidores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leitura_id: Mapped[int] = mapped_column(
        ForeignKey("leituras.id", ondelete="CASCADE"), index=True
    )
    # Texto, nunca inteiro: existem identificadores alfanumericos como F00001.
    numero: Mapped[str] = mapped_column(String(60), index=True)
    ordem: Mapped[int] = mapped_column(Integer)

    leitura: Mapped["Leitura"] = relationship(back_populates="medidores")


class Foto(Base):
    __tablename__ = "fotos"
    __table_args__ = (Index("ix_fotos_id_externo_seq", "id_externo", "sequencia"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes.id", ondelete="CASCADE"), index=True)
    leitura_id: Mapped[int | None] = mapped_column(
        ForeignKey("leituras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    principal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    nome_arquivo: Mapped[str] = mapped_column(String(160), unique=True)
    id_externo: Mapped[str] = mapped_column(String(30))
    sequencia: Mapped[int] = mapped_column(Integer)
    caminho_storage: Mapped[str] = mapped_column(String(300))
    largura: Mapped[int | None] = mapped_column(Integer, nullable=True)
    altura: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lote: Mapped["Lote"] = relationship(back_populates="fotos")
    leitura: Mapped["Leitura | None"] = relationship(back_populates="fotos")
    inferencias: Mapped[list["ResultadoInferencia"]] = relationship(
        back_populates="foto",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ResultadoInferencia.id",
    )


class HistoricoStatus(Base):
    """Trilha append-only de transicoes. Nunca recebe UPDATE nem DELETE direto.

    So `estados.transicionar` e `estados.registrar_estado_inicial` escrevem aqui.
    Ordene sempre por `id`: `criado_em` usa now(), que e o instante da transacao,
    e todas as linhas de uma mesma ingestao compartilham o mesmo valor.
    """

    __tablename__ = "historico_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leitura_id: Mapped[int] = mapped_column(
        ForeignKey("leituras.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    # ingestao | heuristica | modelo:<nome>@v<N> | usuario:<id>
    ator: Mapped[str] = mapped_column(String(80))
    confianca: Mapped[float | None] = mapped_column(Float, nullable=True)
    detalhe: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    leitura: Mapped["Leitura"] = relationship(back_populates="historico")


class Quarentena(Base):
    """Inconsistencia de dados detectada na ingestao. Granularidade: o registro.

    `leitura_id` e nulo para foto orfa; `foto_id` e nulo para inconsistencias
    que nao envolvem imagem. `lote_id` esta sempre preenchido para permitir
    agregacao por lote sem join.
    """

    __tablename__ = "quarentena"
    __table_args__ = (
        Index("ix_quarentena_lote_tipo", "lote_id", "tipo"),
        Index(
            "ix_quarentena_pendente",
            "severidade",
            postgresql_where="resolvido = false",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes.id", ondelete="CASCADE"))
    leitura_id: Mapped[int | None] = mapped_column(
        ForeignKey("leituras.id", ondelete="CASCADE"), nullable=True, index=True
    )
    foto_id: Mapped[int | None] = mapped_column(
        ForeignKey("fotos.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tipo: Mapped[str] = mapped_column(String(40), index=True)
    severidade: Mapped[str] = mapped_column(String(15))
    detalhe: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolvido: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Texto por enquanto: a tabela usuarios so existe a partir do marco M3.
    resolvido_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Acesso (marco M3)
# ---------------------------------------------------------------------------


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    # Sempre em minusculas: normalizado no servico antes de gravar ou consultar.
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(300))
    papel: Mapped[str] = mapped_column(String(20), default="auditor")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Nulo = precisa trocar a senha no proximo login (seed do admin, reset por admin).
    senha_alterada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )


class RefreshToken(Base):
    """Refresh token opaco: so o sha256 fica no banco. Rotacao encadeada."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revogado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    revogado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    substituido_por: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    usuario: Mapped["Usuario"] = relationship(back_populates="refresh_tokens")


class LogAcesso(Base):
    """Append-only. Base do rate limiting e da trilha de auditoria de acesso."""

    __tablename__ = "log_acesso"
    __table_args__ = (
        Index("ix_log_acesso_identificador_evento", "identificador", "evento", "criado_em"),
        Index("ix_log_acesso_ip_evento", "ip_origem", "evento", "criado_em"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    identificador: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evento: Mapped[str] = mapped_column(String(30))
    ip_origem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Fila e inferencia (marco M3)
# ---------------------------------------------------------------------------


class Job(Base):
    """Fila de jobs no proprio Postgres. Claim com FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "ix_jobs_pendentes",
            "agendado_para",
            "id",
            postgresql_where="status = 'pendente'",
        ),
        Index("ix_jobs_status_tipo", "status", "tipo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), default="pendente", server_default="pendente")
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_tentativas: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    agendado_para: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResultadoInferencia(Base):
    """Saida bruta de um detector sobre uma foto. Permite re-decidir sem reabrir imagem."""

    __tablename__ = "resultados_inferencia"
    __table_args__ = (Index("ix_resultados_inferencia_foto_modelo", "foto_id", "modelo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    foto_id: Mapped[int] = mapped_column(ForeignKey("fotos.id", ondelete="CASCADE"))
    modelo: Mapped[str] = mapped_column(String(80), index=True)  # ex.: nulo@v0, heuristica@v1
    saida: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    latencia_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    foto: Mapped["Foto"] = relationship(back_populates="inferencias")


class RevisaoHumana(Base):
    """Decisao de um auditor sobre uma leitura da fila humana. Append-only."""

    __tablename__ = "revisoes_humanas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leitura_id: Mapped[int] = mapped_column(
        ForeignKey("leituras.id", ondelete="CASCADE"), index=True
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), index=True
    )
    decisao: Mapped[str] = mapped_column(String(20))
    motivo: Mapped[str | None] = mapped_column(String(40), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Marcada quando o item foi redistribuido para um segundo revisor (auditoria
    # amostral do marco M8). Fica no esquema desde ja para nao migrar depois.
    redistribuida: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    leitura: Mapped["Leitura"] = relationship(back_populates="revisoes")


# ---------------------------------------------------------------------------
# Rotulagem (marco M6)
# ---------------------------------------------------------------------------


def _lista_sql(enum: type[StrEnum]) -> str:
    return ", ".join(f"'{m.value}'" for m in enum)


class Rotulo(Base):
    """Rotulo humano de uma foto no esquema v2: cena, 5 defeitos e legivel.

    A unidade e a foto, nao a leitura (`fotos.leitura_id` liga as duas). Quem
    rotulou e um usuario da plataforma ou, para a rodada `lab02` importada do app
    `rotulador/`, um identificador externo (`davi_melo`, `ivan`...): exatamente um
    dos dois. Um rotulador tem no maximo um rotulo por foto e rodada; como
    `usuario_id` e `rotulador_externo` sao anulaveis, a regra vive em dois indices
    unicos parciais em vez de uma UNIQUE comum (NULL nunca colide).
    """

    __tablename__ = "rotulos"
    __table_args__ = (
        CheckConstraint(
            "(usuario_id IS NULL) <> (rotulador_externo IS NULL)", name="ck_rotulos_um_rotulador"
        ),
        CheckConstraint(f"rodada IN ({_lista_sql(RodadaRotulagem)})", name="ck_rotulos_rodada"),
        CheckConstraint(f"cena IN ({_lista_sql(Cena)})", name="ck_rotulos_cena"),
        Index(
            "uq_rotulos_foto_rodada_usuario",
            "foto_id",
            "rodada",
            "usuario_id",
            unique=True,
            postgresql_where="usuario_id IS NOT NULL",
        ),
        Index(
            "uq_rotulos_foto_rodada_externo",
            "foto_id",
            "rodada",
            "rotulador_externo",
            unique=True,
            postgresql_where="rotulador_externo IS NOT NULL",
        ),
        # Rodadas a, b e desempate se excluem por usuario e foto: quem votou numa nao
        # vota nas outras. No banco, e nao so no endpoint, para fechar a corrida entre
        # duas abas do mesmo usuario.
        Index(
            "uq_rotulos_foto_usuario_dupla_cega",
            "foto_id",
            "usuario_id",
            unique=True,
            postgresql_where="usuario_id IS NOT NULL AND rodada IN ('a', 'b', 'desempate')",
        ),
        # Cada rodada cega rotula cada foto uma vez: sem isso um segundo rotulador
        # gravaria a mesma foto pelo id e desfaria o empate (fugindo do desempate).
        Index(
            "uq_rotulos_foto_rodada_cega",
            "foto_id",
            "rodada",
            unique=True,
            postgresql_where="rodada IN ('a', 'b', 'desempate')",
        ),
        Index("ix_rotulos_rodada_foto", "rodada", "foto_id"),
        # Fora de `producao` o esquema v2 e completo. Em `producao` (decisao de auditor,
        # M8) cena e os defeitos nao declarados pela decisao ficam nulos.
        CheckConstraint(
            "rodada = 'producao' OR (cena IS NOT NULL AND reflexo IS NOT NULL AND "
            "fora_de_foco IS NOT NULL AND tampa_suja IS NOT NULL AND enquadramento IS NOT NULL "
            "AND display_apagado IS NOT NULL AND legivel IS NOT NULL)",
            name="ck_rotulos_completo",
        ),
        # Rotulo de producao aponta para a decisao que o gerou: a da fila ou a da
        # auditoria amostral, exatamente uma. As outras rodadas nao apontam.
        CheckConstraint(
            "CASE WHEN rodada = 'producao' THEN (revisao_id IS NULL) <> (auditoria_id IS NULL) "
            "ELSE revisao_id IS NULL AND auditoria_id IS NULL END",
            name="ck_rotulos_origem_producao",
        ),
        Index(
            "uq_rotulos_revisao",
            "revisao_id",
            unique=True,
            postgresql_where="revisao_id IS NOT NULL",
        ),
        Index(
            "uq_rotulos_auditoria",
            "auditoria_id",
            unique=True,
            postgresql_where="auditoria_id IS NOT NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    foto_id: Mapped[int] = mapped_column(ForeignKey("fotos.id", ondelete="CASCADE"))
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    rotulador_externo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rodada: Mapped[str] = mapped_column(String(20))
    # Nulos so na rodada `producao` (ck_rotulos_completo).
    cena: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reflexo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fora_de_foco: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tampa_suja: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    enquadramento: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    display_apagado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    legivel: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    segundos_na_foto: Mapped[float | None] = mapped_column(Float, nullable=True)
    # So no lab02: foto de controle (vista pelos 3 integrantes), lida do controle.dat do
    # app, que fica fora do git. Nulo = nao informado.
    controle: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Quando o rotulador decidiu (no lab02, o `gravado_em` do CSV do app).
    gravado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Origem do rotulo `producao` (M8): a decisao da fila ou a da auditoria amostral.
    revisao_id: Mapped[int | None] = mapped_column(
        ForeignKey("revisoes_humanas.id", ondelete="CASCADE", name="fk_rotulos_revisao"),
        nullable=True,
    )
    auditoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("auditorias_amostrais.id", ondelete="CASCADE", name="fk_rotulos_auditoria"),
        nullable=True,
    )

    foto: Mapped["Foto"] = relationship()


# ---------------------------------------------------------------------------
# Auditoria amostral (marco M8)
# ---------------------------------------------------------------------------


class AuditoriaAmostral(Base):
    """Item ja decidido na fila humana, sorteado para um segundo revisor.

    O segundo revisor nao ve a primeira decisao (a resposta do `/auditoria/proxima`
    nao a traz) e nunca e o primeiro: a regra vale no POST e no banco
    (`ck_auditorias_segundo_revisor`, `ck_auditorias_reserva_de_outro`). Uma auditoria
    por leitura. A decisao do segundo revisor nao muda o status da leitura: serve
    para medir a concordancia inter-anotador continua. Depois de gravada, a decisao e
    imutavel no banco (gatilho `trg_auditorias_decisao_imutavel`, da migracao).
    """

    __tablename__ = "auditorias_amostrais"
    __table_args__ = (
        CheckConstraint(
            "CASE WHEN origem = 'fila' THEN revisao_id IS NOT NULL AND revisor_original_id "
            "IS NOT NULL WHEN origem = 'auto_aprovada' THEN revisao_id IS NULL AND "
            "revisor_original_id IS NULL ELSE false END",
            name="ck_auditorias_origem",
        ),
        CheckConstraint(
            "revisor_id IS NULL OR revisor_id <> revisor_original_id",
            name="ck_auditorias_segundo_revisor",
        ),
        CheckConstraint(
            "reservado_por IS NULL OR reservado_por <> revisor_original_id",
            name="ck_auditorias_reserva_de_outro",
        ),
        CheckConstraint(
            "(revisor_id IS NULL) = (decisao IS NULL) "
            "AND (decisao IS NULL) = (decidida_em IS NULL)",
            name="ck_auditorias_decisao_completa",
        ),
        CheckConstraint(
            f"decisao IS NULL OR decisao IN ({_lista_sql(DecisaoHumana)})",
            name="ck_auditorias_decisao",
        ),
        CheckConstraint(
            f"motivo IS NULL OR motivo IN ({_lista_sql(MotivoReprovacao)})",
            name="ck_auditorias_motivo",
        ),
        CheckConstraint(
            "decisao IS DISTINCT FROM 'reprovada' OR motivo IS NOT NULL",
            name="ck_auditorias_reprovada_com_motivo",
        ),
        CheckConstraint(
            "decisao IS DISTINCT FROM 'aprovada' OR motivo IS NULL",
            name="ck_auditorias_aprovada_sem_motivo",
        ),
        UniqueConstraint("leitura_id", name="uq_auditorias_leitura"),
        UniqueConstraint("revisao_id", name="uq_auditorias_revisao"),
        Index(
            "ix_auditorias_pendentes",
            "sorteada_em",
            "id",
            postgresql_where="decisao IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leitura_id: Mapped[int] = mapped_column(ForeignKey("leituras.id", ondelete="CASCADE"))
    # fila: decisao de auditor (revisao e revisor original preenchidos); auto_aprovada:
    # decisao da maquina, sem revisao nem revisor original (ck_auditorias_origem).
    origem: Mapped[str] = mapped_column(String(20), default="fila", server_default="fila")
    revisao_id: Mapped[int | None] = mapped_column(
        ForeignKey("revisoes_humanas.id", ondelete="CASCADE"), nullable=True
    )
    revisor_original_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=True
    )
    sorteada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reservado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    reservado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revisor_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=True
    )
    decisao: Mapped[str | None] = mapped_column(String(20), nullable=True)
    motivo: Mapped[str | None] = mapped_column(String(40), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    decidida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    leitura: Mapped["Leitura"] = relationship()
    revisao: Mapped["RevisaoHumana | None"] = relationship()

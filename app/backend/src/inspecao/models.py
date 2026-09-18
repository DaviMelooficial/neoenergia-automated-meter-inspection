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

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from inspecao.enums import StatusLeitura


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
    __table_args__ = (UniqueConstraint("lote_id", "linha_csv", name="uq_leitura_lote_linha"),)

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


class LeituraMedidor(Base):
    """Um medidor por linha. A coluna de origem traz ate 8 separados por barra."""

    __tablename__ = "leitura_medidores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leitura_id: Mapped[int] = mapped_column(
        ForeignKey("leituras.id", ondelete="CASCADE"), index=True
    )
    # Texto, nunca inteiro: existem identificadores alfanumericos como F50081.
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

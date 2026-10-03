"""Modelos Pydantic da API. Os tipos do frontend sao gerados a partir do OpenAPI."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from inspecao.enums import DecisaoHumana, MotivoReprovacao, Papel


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth


class LoginIn(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UsuarioOut(_ORM):
    id: int
    nome: str
    email: str
    papel: Papel
    ativo: bool
    precisa_trocar_senha: bool = False
    criado_em: datetime


class TrocarSenhaIn(BaseModel):
    senha_atual: str
    senha_nova: str = Field(min_length=10, max_length=200)


class UsuarioIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    email: EmailStr
    senha_inicial: str = Field(min_length=10, max_length=200)
    papel: Papel = Papel.AUDITOR


class UsuarioPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    papel: Papel | None = None
    ativo: bool | None = None
    resetar_senha: str | None = Field(default=None, min_length=10, max_length=200)


# ---------------------------------------------------------------- dominio


class LoteOut(_ORM):
    id: int
    nome: str
    data_extracao: datetime | None
    csv_nome: str
    total_linhas: int
    total_fotos_disco: int
    ingerido_em: datetime
    por_status: dict[str, int] = Field(default_factory=dict)


class NotaOut(_ORM):
    codigo: str
    descricao: str
    exige_foto: bool
    cena_esperada: str | None


class FotoOut(_ORM):
    id: int
    nome_arquivo: str
    sequencia: int
    principal: bool
    largura: int | None
    altura: int | None
    bytes: int
    url: str


class HistoricoOut(_ORM):
    status: str
    ator: str
    confianca: float | None
    detalhe: dict[str, Any] | None
    criado_em: datetime


class InferenciaOut(_ORM):
    modelo: str
    saida: dict[str, Any]
    latencia_ms: int | None
    criado_em: datetime


class QuarentenaOut(_ORM):
    id: int
    lote_id: int
    leitura_id: int | None
    foto_id: int | None
    tipo: str
    severidade: str
    detalhe: dict[str, Any] | None
    resolvido: bool
    resolvido_por: str | None
    resolvido_em: datetime | None
    criado_em: datetime


class RevisaoOut(_ORM):
    id: int
    usuario_id: int
    decisao: DecisaoHumana
    motivo: MotivoReprovacao | None
    observacao: str | None
    criado_em: datetime


class LeituraResumoOut(BaseModel):
    id: int
    lote: str
    linha_csv: int
    medidores: list[str]
    posicao_lida: int | None
    nota_codigo: str | None
    status_atual: str
    foto_principal: FotoOut | None
    reservado_por: int | None
    reservado_ate: datetime | None
    criado_em: datetime


class LeituraOut(LeituraResumoOut):
    nota: NotaOut | None
    fotos: list[FotoOut]
    historico: list[HistoricoOut]
    inferencias: list[InferenciaOut]
    quarentena: list[QuarentenaOut]
    revisoes: list[RevisaoOut]


class Pagina[T](BaseModel):
    itens: list[T]
    total: int
    pagina: int
    por_pagina: int


class DecisaoIn(BaseModel):
    decisao: DecisaoHumana
    motivo: MotivoReprovacao | None = None
    observacao: str | None = Field(default=None, max_length=2000)


class ResolverQuarentenaIn(BaseModel):
    observacao: str | None = Field(default=None, max_length=2000)


class IngestBatchIn(BaseModel):
    lote: str = Field(pattern=r"^PSP_EXTRATLEITIMPL_\d{6}_\d{4}$")
    reprocessar: bool = False


class JobOut(_ORM):
    id: int
    tipo: str
    status: str
    payload: dict[str, Any]
    tentativas: int
    agendado_para: datetime
    criado_em: datetime

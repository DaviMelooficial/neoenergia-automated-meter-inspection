"""Modelos Pydantic da API. Os tipos do frontend sao gerados a partir do OpenAPI."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from inspecao.enums import Cena, DecisaoHumana, Defeito, MotivoReprovacao, Papel, RodadaRotulagem


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


# ---------------------------------------------------------------- rotulagem

# Rodadas feitas na tela. `lab02` so entra pelo importador e `producao` pela fila (M8).
RodadaTela = Literal["a", "b", "desempate"]


class FotoRotulagemOut(BaseModel):
    """O que o rotulador recebe. Lista fechada (extra="forbid"): nada de leitura,
    nota, medidor, score, decisao da maquina nem rotulo de outra pessoa. Cegueira
    e regra de servidor; o teste de vazamento confere exatamente estes campos."""

    model_config = ConfigDict(extra="forbid")

    foto_id: int
    url: str
    largura: int | None
    altura: int | None
    rodada: RodadaTela
    restantes: int


class RotuloIn(BaseModel):
    """Esquema v2. As regras de consistencia sao recusadas (422), nao corrigidas."""

    rodada: RodadaTela
    cena: Cena
    defeitos: list[Defeito] = Field(default_factory=list, max_length=len(Defeito))
    legivel: bool
    segundos_na_foto: float | None = Field(default=None, ge=0, le=3600)

    @model_validator(mode="after")
    def _consistencia_v2(self) -> "RotuloIn":
        if len(set(self.defeitos)) != len(self.defeitos):
            raise ValueError("defeito repetido")
        if self.cena is Cena.NAO_MEDIDOR and self.defeitos:
            raise ValueError("nao_medidor nao tem defeito")
        if self.cena in (Cena.NAO_MEDIDOR, Cena.OCORRENCIA) and self.legivel:
            raise ValueError(f"{self.cena.value} implica legivel = nao")
        if Defeito.DISPLAY_APAGADO in self.defeitos and self.legivel:
            raise ValueError("display_apagado implica legivel = nao")
        return self


class RotuloOut(BaseModel):
    """Devolve so o rotulo do proprio usuario, que ele acabou de gravar."""

    id: int
    foto_id: int
    rodada: RodadaRotulagem
    cena: Cena
    defeitos: list[Defeito]
    legivel: bool
    gravado_em: datetime


class KappaClasseOut(BaseModel):
    classe: str
    kappa: float | None
    n: int = Field(description="pares-foto na media ponderada")
    passa: bool


class KappaParOut(BaseModel):
    a: str
    b: str
    n: int
    kappa: dict[str, float | None]


class ConsolidacaoOut(BaseModel):
    rodadas: list[RodadaRotulagem]
    limiar: float
    rotuladores: list[str]
    rotulos: int
    fotos: int
    fotos_dupla_rotulagem: int
    fotos_com_empate: int
    por_classe: list[KappaClasseOut]
    pares: list[KappaParOut]

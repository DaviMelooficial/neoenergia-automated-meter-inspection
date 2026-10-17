"""Modelos Pydantic da API. Os tipos do frontend sao gerados a partir do OpenAPI."""

from datetime import date, datetime
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


# ---------------------------------------------------------------- metricas (M8)

BucketDecisao = Literal[
    "decidida_conta_teste",
    "auto_rejeitada",
    "auto_aprovada",
    "fila_humana",
    "aprovada_humano",
    "reprovada_humano",
    "em_processamento",
]


class DecisoesOut(BaseModel):
    """Contagem por bucket de decisao e taxa sobre as leituras com foto."""

    com_foto: int
    contagens: dict[BucketDecisao, int]
    taxas: dict[BucketDecisao, float | None]


class BaselineQualidadeOut(DecisoesOut):
    lotes: list[str]


class DecisoesLoteOut(DecisoesOut):
    lote: str
    data_extracao: date | None


class DecisoesDetectorOut(DecisoesOut):
    detector: str
    politica: str


class MatrizConcordanciaOut(BaseModel):
    """maquina_humano: `rejeita` = p_ruim >= limiar_rejeita; `ilegivel` = humano."""

    rejeita_ilegivel: int
    rejeita_legivel: int
    passa_ilegivel: int
    passa_legivel: int


class ConcordanciaMaquinaOut(BaseModel):
    fonte: Literal["producao", "lab02"]
    escopo: str
    politica: str
    n: int
    matriz: MatrizConcordanciaOut
    concordancia: float | None
    kappa: float | None


class PeriodoOut(BaseModel):
    de: date | None
    ate: date | None


class AutoDecididasOut(BaseModel):
    quantidade: int
    percentual: float


class FilaResumoOut(BaseModel):
    pendentes: int
    decididas: int


class ProdutividadeOut(BaseModel):
    usuario_id: int
    decisoes: int


class QualidadeOut(BaseModel):
    periodo: PeriodoOut
    total_leituras: int
    com_foto: int
    sem_foto: int
    auto_decididas: AutoDecididasOut
    fila_humana: FilaResumoOut
    por_status: dict[str, int]
    quarentena_por_tipo: dict[str, int]
    defeitos_qualidade: dict[str, int]
    revisoes_por_decisao: dict[str, int]
    produtividade_por_auditor: list[ProdutividadeOut]
    tempo_medio_decisao_segundos: float | None
    decididas_conta_teste: int = Field(description="decisoes de E2E/demo, fora das medidas")
    jobs: dict[str, dict[str, int]]
    baseline: BaselineQualidadeOut
    por_lote: list[DecisoesLoteOut]
    por_detector: list[DecisoesDetectorOut]
    concordancia_maquina_humano: list[ConcordanciaMaquinaOut]


class SinalOut(BaseModel):
    """`desvio` = valor - baseline. `n` = denominador do valor no lote. `z`: carta p com
    sobredispersao (so nas fracoes: classes, auto-rejeicao e zona cinzenta)."""

    valor: float | None
    baseline: float | None
    desvio: float | None
    alerta: bool
    n: int
    z: float | None


class SinaisLoteOut(BaseModel):
    classes: dict[str, SinalOut]
    taxa_auto_rejeicao: SinalOut
    confianca_media: SinalOut
    discordancia: SinalOut
    zona_cinzenta: SinalOut


class LoteDriftOut(BaseModel):
    lote_id: int
    lote: str
    data_extracao: date | None
    na_baseline: bool
    total: int
    com_foto: int
    por_status: dict[str, int]
    quarentena_por_tipo: dict[str, int]
    modelos: list[str]
    taxa_reprovacao_humana: float | None
    n_fotos_com_score: int
    amostra_insuficiente: bool
    p_ruim_medio: float | None
    sinais: SinaisLoteOut
    alertas: list[str]


class ValoresBaselineOut(BaseModel):
    classes: dict[str, float | None]
    taxa_auto_rejeicao: float | None
    zona_cinzenta: float | None
    p_ruim_medio: float | None
    confianca_media: float | None
    discordancia: float | None


class BaselineDriftOut(BaseModel):
    lotes: list[str]
    n_fotos_com_score: int
    n_pares_discordancia: int
    valores: ValoresBaselineOut | None
    sobredispersao: dict[str, float] = Field(
        description="phi APLICADO na carta p (1 com menos de min_lotes_phi lotes na baseline)"
    )
    sobredispersao_estimada: dict[str, float] = Field(
        description="phi estimado entre os lotes da baseline (qui-quadrado/(k-1)), informativo"
    )


class CriterioDriftOut(BaseModel):
    min_fotos: int
    min_pares_discordancia: int
    classe_ativa: float
    inicio_zona_cinzenta: float
    desvio_classe: float
    desvio_rejeicao: float
    lotes_seguidos_rejeicao: int
    queda_confianca: float
    lotes_seguidos_confianca: int
    subida_discordancia: float
    subida_zona_cinzenta: float
    z_carta_p: float
    min_lotes_phi: int


class DriftOut(BaseModel):
    politica: str
    detector: str | None
    limiar_rejeita: float | None
    criterio: CriterioDriftOut
    baseline: BaselineDriftOut
    lotes: list[LoteDriftOut]


class DiagnosticoZonaOut(BaseModel):
    """Erro que a faixa p_ruim <= limiar teria no Lab 2 (dentro da amostra). Nao e limiar."""

    limiar: float
    fonte: Literal["lab02"]
    n: int
    ilegiveis: int
    taxa_erro: float | None
    limite_inferior_erro: float | None


class AutoAprovacaoOut(BaseModel):
    politica: str
    detector: str | None
    limiar_rejeita: float | None
    limiar_aprova: float | None
    ativa: bool
    contador_aplicavel: bool
    motivo: str
    alvo_erro: float
    confianca: float
    necessarias: int
    fotos_na_zona: int | None
    rotuladas_na_zona: int
    erros: int
    acumuladas_sem_erro: int
    limite_superior_erro: float | None
    meta_atingida: bool
    diagnostico: DiagnosticoZonaOut | None


# ---------------------------------------------------------------- auditoria (M8)


class QuarentenaCegaOut(BaseModel):
    """Inconsistencia da leitura sem nenhum id (nem da leitura, nem da foto)."""

    model_config = ConfigDict(extra="forbid")

    tipo: str
    severidade: str
    detalhe: dict[str, Any] | None


class ItemAuditoriaOut(BaseModel):
    """O que o segundo revisor recebe, igual para as origens fila, auto_aprovada e
    auto_rejeitada. Lista fechada (extra="forbid"): nada de status, historico, revisao
    anterior, scores da maquina nem id da leitura. O teste de vazamento confere."""

    model_config = ConfigDict(extra="forbid")

    auditoria_id: int
    lote: str
    linha_csv: int
    medidores: list[str]
    posicao_lida: int | None
    nota_codigo: str | None
    nota: NotaOut | None
    foto_principal: FotoOut | None
    fotos: list[FotoOut]
    quarentena: list[QuarentenaCegaOut]
    reservado_ate: datetime | None
    restantes: int


class AuditoriaDecisaoOut(BaseModel):
    """So a decisao do proprio segundo revisor."""

    auditoria_id: int
    decisao: DecisaoHumana
    motivo: MotivoReprovacao | None
    observacao: str | None
    decidida_em: datetime


class MedidasConcordanciaOut(BaseModel):
    n: int | None
    concordancia_decisao: float | None
    kappa_decisao: float | None
    concordancia_motivo: float | None
    kappa_motivo: float | None


class TotalConcordanciaOut(MedidasConcordanciaOut):
    """`suprimido`: menos de `min_pares` pares; o supervisor nao ve a medida."""

    suprimido: bool


class DecisaoAutomaticaAuditoriaOut(BaseModel):
    """Auditorias de decisao da maquina. Erro: auto-aprovada reprovada pelo revisor, ou
    auto-rejeitada aprovada pelo revisor (falso positivo)."""

    n: int | None
    erros: int | None
    taxa_erro: float | None
    suprimido: bool


class ConcordanciaLoteOut(MedidasConcordanciaOut):
    lote: str


class ConcordanciaDiaOut(MedidasConcordanciaOut):
    dia: date


class ConcordanciaAuditoriaOut(BaseModel):
    """`detalhe` = admin: contagens, por lote e serie. Supervisor: so totais >= min_pares."""

    taxa_configurada: float
    min_pares: int
    detalhe: bool
    decisoes_na_fila: int | None
    sorteadas: int | None
    taxa_observada: float | None
    pendentes: int | None
    decididas: int | None
    total: TotalConcordanciaOut
    auto_aprovadas: DecisaoAutomaticaAuditoriaOut
    auto_rejeitadas: DecisaoAutomaticaAuditoriaOut
    por_lote: list[ConcordanciaLoteOut]
    serie: list[ConcordanciaDiaOut]

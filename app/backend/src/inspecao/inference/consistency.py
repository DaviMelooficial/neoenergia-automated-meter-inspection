"""Regra deterministica de consistencia entre leitura declarada, nota e foto.

Nao e ML. Roda na ingestao, antes de qualquer modelo, e e o primeiro valor
entregue pelo sistema (marco M2). Funcao pura: recebe fatos ja extraidos e
devolve o veredito, sem tocar em banco nem em imagem, para ser testavel com
uma linha de codigo.

Referencias: doc 03 (arquitetura), secao 6; plano 03 (backend), secao 6;
plano 02 (dados), secao 6.
"""

from dataclasses import dataclass

from inspecao.enums import Severidade, StatusLeitura, TipoInconsistencia

# Notas cuja descricao pressupoe que uma leitura foi feita. Se a posicao vier
# vazia junto com uma delas, ha contradicao entre os campos do CSV.
NOTAS_PRESSUPOEM_LEITURA: frozenset[str] = frozenset({"T111", "M101", "B101"})


@dataclass(frozen=True, slots=True)
class Achado:
    tipo: TipoInconsistencia
    severidade: Severidade
    detalhe: dict | None = None


@dataclass(frozen=True, slots=True)
class Veredito:
    """`novo_status` None significa: permanece `recebida` e segue para triagem."""

    novo_status: StatusLeitura | None
    achados: tuple[Achado, ...]

    @property
    def bloqueante(self) -> bool:
        return any(a.severidade is Severidade.BLOQUEANTE for a in self.achados)

    @property
    def segue_para_triagem(self) -> bool:
        return self.novo_status is None


def avaliar(
    *,
    nota_codigo: str | None,
    nota_conhecida: bool,
    nota_exige_foto: bool,
    tem_foto: bool,
    foto_referenciada_ausente: bool = False,
    posicao_lida: int | None = None,
    posicao_invalida: bool = False,
    quantidade_medidores: int = 1,
) -> Veredito:
    """Aplica as regras de consistencia a uma leitura.

    Parametros ja normalizados pelo parser e pelo cruzamento com a planilha:
    - `nota_conhecida`: o codigo existe em `notas_ocorrencia`.
    - `nota_exige_foto`: valor da planilha; ignorado quando a nota e desconhecida.
    - `tem_foto`: a foto referenciada existe em disco e foi importada.
    - `foto_referenciada_ausente`: o CSV aponta uma foto que nao existe em disco.
    """
    achados: list[Achado] = []

    if nota_codigo is not None and not nota_conhecida:
        achados.append(
            Achado(
                TipoInconsistencia.CODIGO_NAO_MAPEADO,
                Severidade.INFORMATIVO,
                {"nota_codigo": nota_codigo},
            )
        )

    # Sem a planilha nao ha como saber se a nota exige foto: nao acusar.
    exige_foto = nota_conhecida and nota_exige_foto

    if foto_referenciada_ausente:
        # O CSV prometeu evidencia que nao chegou. Nao fatura, nao vai a triagem.
        # Um so achado (a causa raiz), mesmo que a nota exija foto: o total que
        # bloqueia faturamento e nota_exige_sem_foto + foto_referenciada_ausente.
        achados.append(
            Achado(TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE, Severidade.BLOQUEANTE)
        )
        novo_status: StatusLeitura | None = StatusLeitura.SEM_FOTO
    elif not tem_foto:
        if exige_foto:
            achados.append(
                Achado(
                    TipoInconsistencia.NOTA_EXIGE_SEM_FOTO,
                    Severidade.BLOQUEANTE,
                    {"nota_codigo": nota_codigo},
                )
            )
        novo_status = StatusLeitura.SEM_FOTO
    else:
        if nota_conhecida and not nota_exige_foto:
            achados.append(
                Achado(
                    TipoInconsistencia.FOTO_SEM_EXIGENCIA,
                    Severidade.INFORMATIVO,
                    {"nota_codigo": nota_codigo},
                )
            )
        novo_status = None  # segue para triagem de qualidade

    if quantidade_medidores > 1:
        achados.append(
            Achado(
                TipoInconsistencia.MEDIDOR_MULTIPLO,
                Severidade.INFORMATIVO,
                {"quantidade": quantidade_medidores},
            )
        )

    if posicao_invalida:
        achados.append(Achado(TipoInconsistencia.POSICAO_INVALIDA, Severidade.INFORMATIVO))
    elif posicao_lida is None and nota_codigo in NOTAS_PRESSUPOEM_LEITURA:
        achados.append(
            Achado(
                TipoInconsistencia.POSICAO_AUSENTE,
                Severidade.INFORMATIVO,
                {"nota_codigo": nota_codigo},
            )
        )

    return Veredito(novo_status=novo_status, achados=tuple(achados))

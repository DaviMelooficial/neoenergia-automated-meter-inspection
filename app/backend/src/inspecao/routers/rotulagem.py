"""Rotulagem dupla-cega (marco M6), esquema v2 do Lab 2.

Supervisor ou acima rotula e ve o kappa do `lab02`. O kappa das rodadas cegas
(a, b, desempate) e so de admin ou de quem nao votou nelas: com n pequeno, o
kappa por par antes e depois do proprio voto revela o voto do outro. O export traz
o rotulo consolidado de cada foto; por isso e so de admin.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inspecao.auth.deps import Admin, Supervisor
from inspecao.db import get_db
from inspecao.enums import Defeito, RodadaRotulagem
from inspecao.models import Foto, Rotulo
from inspecao.rotulagem import servico
from inspecao.rotulagem.consolidacao import CLASSES, DEFEITOS, LIMIAR_KAPPA
from inspecao.schemas import (
    ConsolidacaoOut,
    FotoRotulagemOut,
    KappaClasseOut,
    KappaParOut,
    RodadaTela,
    RotuloIn,
    RotuloOut,
)

router = APIRouter(prefix="/rotulagem", tags=["rotulagem"])
DB = Annotated[Session, Depends(get_db)]
Rodadas = Annotated[list[RodadaRotulagem] | None, Query(max_length=5)]


def _rotulo_saida(r: Rotulo) -> RotuloOut:
    return RotuloOut(
        id=r.id,
        foto_id=r.foto_id,
        rodada=RodadaRotulagem(r.rodada),
        cena=r.cena,
        defeitos=[Defeito(d) for d in DEFEITOS if getattr(r, d)],
        legivel=r.legivel,
        gravado_em=r.gravado_em,
    )


def _colisao(erro: servico.ColisaoDeRotulador, codigo: int) -> HTTPException:
    return HTTPException(codigo, str(erro))


@router.post("/proxima", response_model=FotoRotulagemOut)
def proxima(db: DB, usuario: Supervisor, rodada: RodadaTela = "a") -> FotoRotulagemOut:
    """Proxima foto que este usuario ainda pode rotular nesta rodada.

    A resposta so identifica a foto: nenhum rotulo alheio, decisao da maquina,
    score, posicao lida nem nota do leiturista.
    """
    try:
        foto, restantes = servico.proxima_foto(db, usuario.id, RodadaRotulagem(rodada))
    except servico.ColisaoDeRotulador as erro:
        # Dado inconsistente em a/b (so por escrita fora da API): sem desempate ate corrigir.
        raise _colisao(erro, status.HTTP_409_CONFLICT) from erro
    if foto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nada a rotular nesta rodada")
    return FotoRotulagemOut(
        foto_id=foto.id,
        url=f"/api/fotos/{foto.id}/arquivo",
        largura=foto.largura,
        altura=foto.altura,
        rodada=rodada,
        restantes=restantes,
    )


@router.get("/consolidacao", response_model=ConsolidacaoOut)
def consolidacao(db: DB, usuario: Supervisor, rodada: Rodadas = None) -> ConsolidacaoOut:
    """Kappa de Cohen por classe (media dos pares ponderada por n) e por par.

    Padrao: rodada `lab02`. Rodadas cegas: 403 para quem votou em alguma delas
    (exceto admin). Juntar rodadas em que o mesmo rotulador votou duas vezes na
    mesma foto devolve 422, porque inflaria a concordancia.
    """
    rodadas = rodada or [RodadaRotulagem.LAB02]
    if not servico.pode_ver_consolidacao(db, usuario, rodadas):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "voce votou nestas rodadas: o kappa delas so fica visivel para admin",
        )
    try:
        r = servico.resumir(db, rodadas)
    except servico.ColisaoDeRotulador as erro:
        raise _colisao(erro, status.HTTP_422_UNPROCESSABLE_CONTENT) from erro
    return ConsolidacaoOut(
        rodadas=rodadas,
        limiar=LIMIAR_KAPPA,
        rotuladores=r.rotuladores,
        rotulos=r.rotulos,
        fotos=r.fotos,
        fotos_dupla_rotulagem=r.fotos_dupla,
        fotos_com_empate=r.fotos_empate,
        por_classe=[
            KappaClasseOut(classe=c, kappa=r.medio[c].kappa, n=r.medio[c].n, passa=r.medio[c].passa)
            for c in CLASSES
        ],
        pares=[KappaParOut(a=p.a, b=p.b, n=p.n, kappa=p.kappa) for p in r.pares],
    )


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "rotulos.csv consolidado"}},
)
def exportar(db: DB, _: Admin, rodada: Rodadas = None) -> Response:
    """Uma linha por foto, rotulo por maioria; empate fica vazio. Mesmo arquivo do
    `make rotulos-exportar`, que grava em `ml/artifacts/rotulos.csv`."""
    try:
        texto, _n = servico.exportar_csv(db, rodada or [RodadaRotulagem.LAB02])
    except servico.ColisaoDeRotulador as erro:
        raise _colisao(erro, status.HTTP_422_UNPROCESSABLE_CONTENT) from erro
    return Response(
        texto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rotulos.csv"'},
    )


@router.post("/{foto_id}", response_model=RotuloOut, status_code=status.HTTP_201_CREATED)
def gravar(foto_id: int, dados: RotuloIn, db: DB, usuario: Supervisor) -> RotuloOut:
    """Grava o rotulo do usuario atual. Um por foto e rodada; nao ha edicao."""
    rodada = RodadaRotulagem(dados.rodada)
    if db.get(Foto, foto_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "foto nao encontrada")
    ja_tem = db.scalar(
        select(Rotulo.id).where(
            Rotulo.foto_id == foto_id,
            Rotulo.rodada == rodada.value,
            Rotulo.usuario_id == usuario.id,
        )
    )
    if ja_tem is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "voce ja rotulou esta foto nesta rodada")
    try:
        elegivel = servico.foto_elegivel(db, usuario.id, rodada, foto_id)
    except servico.ColisaoDeRotulador as erro:
        raise _colisao(erro, status.HTTP_409_CONFLICT) from erro
    if not elegivel:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "foto fora do que voce pode rotular nesta rodada"
        )

    rotulo = Rotulo(
        foto_id=foto_id,
        usuario_id=usuario.id,
        rodada=rodada.value,
        cena=dados.cena.value,
        legivel=dados.legivel,
        segundos_na_foto=dados.segundos_na_foto,
        **{d: Defeito(d) in dados.defeitos for d in DEFEITOS},
    )
    db.add(rotulo)
    try:
        db.commit()
    except IntegrityError as erro:
        # Corrida com outra aba do mesmo usuario: os indices unicos parciais barram o
        # segundo voto na mesma rodada e o voto em outra rodada cega da mesma foto.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "voce ja rotulou esta foto nesta ou em outra rodada cega"
        ) from erro
    db.refresh(rotulo)
    return _rotulo_saida(rotulo)

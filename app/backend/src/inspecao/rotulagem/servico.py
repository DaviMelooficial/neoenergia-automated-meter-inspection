"""Regras de rotulagem que dependem do banco: pool de cada rodada, votos e export.

Amostra de rotulagem: enquanto a amostra estratificada de T-M6-02 nao existe, a
amostra das rodadas `a` e `b` e o conjunto de fotos rotuladas no Lab 2 (`lab02`,
329 fotos). E sobre ele que o esquema v2 precisa ser remedido.

Regras das rodadas cegas, valendo na leitura (`/proxima`) e na escrita (POST),
com um indice unico parcial no banco atras de cada uma:
- cada rodada rotula cada foto uma vez: foto com rotulo naquela rodada, de
  qualquer pessoa, nao e entregue nem aceita (`uq_rotulos_foto_rodada_cega`);
- `a`, `b` e `desempate` se excluem por usuario e foto: quem votou numa nao vota
  nas outras (`uq_rotulos_foto_usuario_dupla_cega`);
- `a` e `b`: foto da amostra;
- `desempate`: foto com rotulo em `a` e em `b` cuja maioria empata em alguma
  classe. E um terceiro voto cego: a resposta tambem nao traz as versoes
  divergentes (desvio consciente da regra 4 do doc 05, secao 4, pendente de
  decisao).

Cegueira tambem vale para os agregados, e a regra e simples: o kappa e a
consolidacao de qualquer consulta que inclua `a`, `b` ou `desempate` sao so de
admin, e admin nao rotula nessas rodadas. Decidir pelo voto passado nao basta: o
terceiro do desempate nunca votou em a/b, consultaria a+b e depois receberia a
foto divergente ja sabendo os votos; quem votou na a compararia `lab02+b` com o
`rotulos.csv` versionado.

A ordem do `/proxima` e propria de cada rotulador: md5 de salt secreto do
servidor (`ROTULAGEM_SALT`), foto e usuario. Com uma ordem unica, ou com md5 sem
salt sobre ids sequenciais, um rotulador calcularia qual foto o outro recebe.
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from inspecao.config import get_settings
from inspecao.enums import ORDEM_PAPEL, Papel, RodadaRotulagem
from inspecao.models import Foto, Lote, Rotulo, Usuario
from inspecao.rotulagem.consolidacao import (
    CLASSES,
    DEFEITOS,
    KappaClasse,
    KappaPar,
    PorFoto,
    consolidar,
    kappa_medio,
    kappa_por_par,
)

RODADAS_DA_TELA = (RodadaRotulagem.A, RodadaRotulagem.B, RodadaRotulagem.DESEMPATE)
AMOSTRA = RodadaRotulagem.LAB02


class ColisaoDeRotulador(ValueError):
    """O mesmo rotulador votaria duas vezes na mesma foto: inflaria a maioria e o kappa."""


def rotulador_de(rotulo: Rotulo) -> str:
    return rotulo.rotulador_externo or f"usuario:{rotulo.usuario_id}"


def voto_de(rotulo: Rotulo) -> dict[str, str | int]:
    voto: dict[str, str | int] = {"cena": rotulo.cena, "legivel": int(rotulo.legivel)}
    for d in DEFEITOS:
        voto[d] = int(getattr(rotulo, d))
    return voto


def votos(db: Session, rodadas: list[RodadaRotulagem]) -> PorFoto:
    """foto_id -> rotulador -> voto, juntando as rodadas pedidas."""
    por_foto: dict[int, dict[str, dict[str, str | int]]] = defaultdict(dict)
    consulta = select(Rotulo).where(Rotulo.rodada.in_([r.value for r in rodadas]))
    for r in db.scalars(consulta.order_by(Rotulo.id)):
        quem = rotulador_de(r)
        if quem in por_foto[r.foto_id]:
            raise ColisaoDeRotulador(
                f"{quem} tem mais de um rotulo da foto {r.foto_id} nas rodadas pedidas"
            )
        por_foto[r.foto_id][quem] = voto_de(r)
    return por_foto


def _rotulou(usuario_id: int, rodadas: list[RodadaRotulagem]):
    return exists().where(
        Rotulo.foto_id == Foto.id,
        Rotulo.usuario_id == usuario_id,
        Rotulo.rodada.in_([r.value for r in rodadas]),
    )


def _tem_rotulo_na_rodada(rodada: RodadaRotulagem):
    return exists().where(Rotulo.foto_id == Foto.id, Rotulo.rodada == rodada.value)


def fotos_divergentes(db: Session) -> list[int]:
    """Fotos com rotulo em `a` e em `b` cuja maioria das duas rodadas empata."""
    por_foto = votos(db, [RodadaRotulagem.A, RodadaRotulagem.B])
    com_a = set(db.scalars(select(Rotulo.foto_id).where(Rotulo.rodada == RodadaRotulagem.A.value)))
    com_b = set(db.scalars(select(Rotulo.foto_id).where(Rotulo.rodada == RodadaRotulagem.B.value)))
    duplas = {f: por_foto[f] for f in com_a & com_b}
    return [c.foto for c in consolidar(duplas) if c.empate]


def condicao_elegivel(db: Session, usuario_id: int, rodada: RodadaRotulagem):
    """Foto que este usuario pode rotular nesta rodada. A mesma condicao filtra o
    `/proxima` e valida o POST: a regra nao depende de por onde o rotulo chega."""
    outras = [r for r in RODADAS_DA_TELA if r is not rodada]
    if rodada is RodadaRotulagem.DESEMPATE:
        candidatas = Foto.id.in_(fotos_divergentes(db))
    else:
        candidatas = _tem_rotulo_na_rodada(AMOSTRA)
    return and_(candidatas, ~_tem_rotulo_na_rodada(rodada), ~_rotulou(usuario_id, outras))


def proxima_foto(db: Session, usuario_id: int, rodada: RodadaRotulagem) -> tuple[Foto | None, int]:
    """Proxima foto da rodada e quantas ainda restam para este usuario.

    A ordem e o md5 de "salt:foto:usuario", com o salt secreto do servidor: estavel
    entre chamadas, intercala os lotes, e diferente e nao calculavel para cada
    rotulador. `restantes` cai quando outra pessoa vota numa foto da rodada.
    """
    filtro = condicao_elegivel(db, usuario_id, rodada)
    salt = get_settings().salt_da_rotulagem()
    restantes = db.scalar(select(func.count()).select_from(Foto).where(filtro)) or 0
    foto = db.scalars(
        select(Foto)
        .where(filtro)
        .order_by(func.md5(func.concat(salt, ":", Foto.id, ":", usuario_id)), Foto.id)
        .limit(1)
    ).first()
    return foto, restantes


def foto_elegivel(db: Session, usuario_id: int, rodada: RodadaRotulagem, foto_id: int) -> bool:
    consulta = select(Foto.id).where(Foto.id == foto_id, condicao_elegivel(db, usuario_id, rodada))
    return db.scalar(consulta) is not None


def eh_admin(usuario: Usuario) -> bool:
    return ORDEM_PAPEL[Papel(usuario.papel)] >= ORDEM_PAPEL[Papel.ADMIN]


def tem_rodada_cega(rodadas: list[RodadaRotulagem]) -> bool:
    return any(r in RODADAS_DA_TELA for r in rodadas)


def pode_ver_consolidacao(usuario: Usuario, rodadas: list[RodadaRotulagem]) -> bool:
    """Rodada cega na consulta (sozinha ou com lab02): so admin. O lab02 ja fechou."""
    return not tem_rodada_cega(rodadas) or eh_admin(usuario)


def pode_rotular(usuario: Usuario, rodada: RodadaRotulagem) -> bool:
    """Admin ve o kappa e o consolidado das rodadas cegas, entao nao vota nelas."""
    return rodada not in RODADAS_DA_TELA or not eh_admin(usuario)


def controle_por_foto(db: Session, foto_ids: list[int]) -> dict[int, bool]:
    """Foto de controle do Lab 2, quando o importador recebeu o controle.dat."""
    consulta = (
        select(Rotulo.foto_id, func.bool_or(Rotulo.controle))
        .where(
            Rotulo.rodada == AMOSTRA.value,
            Rotulo.controle.is_not(None),
            Rotulo.foto_id.in_(foto_ids),
        )
        .group_by(Rotulo.foto_id)
    )
    return dict(db.execute(consulta).tuples().all())


COLUNAS_EXPORT = ["foto_id", "nome_arquivo", "lote", "n_rotuladores", "controle"]
COLUNAS_EXPORT += [*CLASSES, "empate"]


def exportar_csv(db: Session, rodadas: list[RodadaRotulagem]) -> tuple[str, int]:
    """Uma linha por foto, rotulo por maioria; empate fica vazio (NA no pandas).

    `controle` e 1/0 como no `rotulador/consolidar.py`, e vazio quando o controle.dat
    nao foi importado (o arquivo fica fora do git para manter os rotuladores cegos).
    """
    por_foto = votos(db, rodadas)
    fotos = {
        f.id: (f.nome_arquivo, lote)
        for f, lote in db.execute(
            select(Foto, Lote.nome).join(Lote).where(Foto.id.in_(list(por_foto)))
        )
    }
    saida = io.StringIO()
    escritor = csv.DictWriter(saida, fieldnames=COLUNAS_EXPORT, lineterminator="\n")
    escritor.writeheader()
    consolidados = consolidar(por_foto)
    controle = controle_por_foto(db, list(por_foto))
    for c in sorted(consolidados, key=lambda c: fotos[c.foto][0]):
        nome, lote = fotos[c.foto]
        linha = {"foto_id": c.foto, "nome_arquivo": nome, "lote": lote}
        linha |= {"n_rotuladores": c.n_rotuladores, "empate": int(c.empate)}
        linha["controle"] = int(controle[c.foto]) if c.foto in controle else ""
        linha |= {k: "" if v is None else v for k, v in c.valores.items()}
        escritor.writerow(linha)
    return saida.getvalue(), len(consolidados)


@dataclass(frozen=True, slots=True)
class Resumo:
    rodadas: list[RodadaRotulagem]
    rotuladores: list[str]
    rotulos: int
    fotos: int
    fotos_dupla: int
    fotos_empate: int
    controle_com_dupla: int | None  # None sem controle.dat importado
    pares: list[KappaPar]
    medio: dict[str, KappaClasse]


def resumir(db: Session, rodadas: list[RodadaRotulagem]) -> Resumo:
    """Kappa e contagens de uma consolidacao. So agregados."""
    por_foto = votos(db, rodadas)
    pares = kappa_por_par(por_foto)
    controle = controle_por_foto(db, list(por_foto))
    return Resumo(
        rodadas=list(rodadas),
        rotuladores=sorted({r for v in por_foto.values() for r in v}),
        rotulos=sum(len(v) for v in por_foto.values()),
        fotos=len(por_foto),
        fotos_dupla=sum(1 for v in por_foto.values() if len(v) >= 2),
        fotos_empate=sum(1 for c in consolidar(por_foto) if c.empate),
        controle_com_dupla=sum(1 for f, v in por_foto.items() if len(v) >= 2 and controle.get(f))
        if controle
        else None,
        pares=pares,
        medio=kappa_medio(pares),
    )


def contagem_por_rodada(db: Session) -> dict[str, int]:
    consulta = select(Rotulo.rodada, func.count()).group_by(Rotulo.rodada)
    return dict(db.execute(consulta).tuples().all())

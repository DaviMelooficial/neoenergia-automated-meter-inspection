"""Regras de rotulagem que dependem do banco: pool de cada rodada, votos e export.

Amostra de rotulagem: enquanto a amostra estratificada de T-M6-02 nao existe, a
amostra das rodadas `a` e `b` e o conjunto de fotos rotuladas no Lab 2 (`lab02`,
329 fotos). E sobre ele que o esquema v2 precisa ser remedido.

Regras de cada rodada (garantidas aqui, nao na interface):
- `a` e `b`: foto da amostra ainda sem rotulo naquela rodada. Quem rotulou a foto
  numa delas nao a recebe na outra (rodadas com rotuladores distintos).
- `desempate`: foto com rotulo em `a` e em `b` cuja maioria empata em alguma
  classe, ainda sem desempate, para um terceiro que nao rotulou a nem b. O
  desempate e um terceiro voto cego: a resposta tambem nao traz as versoes
  divergentes (desvio consciente da regra 4 do doc 05, secao 4).
"""

import csv
import io
from collections import defaultdict

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from inspecao.enums import RodadaRotulagem
from inspecao.models import Foto, Lote, Rotulo
from inspecao.rotulagem.consolidacao import CLASSES, DEFEITOS, PorFoto, consolidar

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
    """Foto que este usuario pode rotular nesta rodada (sem olhar quem ja rotulou)."""
    if rodada is RodadaRotulagem.DESEMPATE:
        return and_(
            Foto.id.in_(fotos_divergentes(db)),
            ~_rotulou(usuario_id, [RodadaRotulagem.A, RodadaRotulagem.B]),
        )
    outra = RodadaRotulagem.B if rodada is RodadaRotulagem.A else RodadaRotulagem.A
    return and_(_tem_rotulo_na_rodada(AMOSTRA), ~_rotulou(usuario_id, [outra]))


def proxima_foto(db: Session, usuario_id: int, rodada: RodadaRotulagem) -> tuple[Foto | None, int]:
    """Proxima foto da rodada e quantas ainda restam para este usuario.

    Fica de fora a foto que ja tem rotulo nesta rodada, de qualquer pessoa: cada
    rodada rotula cada foto uma vez. A ordem e pelo md5 do nome do arquivo, que
    intercala os lotes e e estavel entre chamadas.
    """
    filtro = and_(
        condicao_elegivel(db, usuario_id, rodada),
        ~_tem_rotulo_na_rodada(rodada),
        ~_rotulou(usuario_id, [rodada]),
    )
    restantes = db.scalar(select(func.count()).select_from(Foto).where(filtro)) or 0
    foto = db.scalars(
        select(Foto).where(filtro).order_by(func.md5(Foto.nome_arquivo), Foto.id).limit(1)
    ).first()
    return foto, restantes


def foto_elegivel(db: Session, usuario_id: int, rodada: RodadaRotulagem, foto_id: int) -> bool:
    consulta = select(Foto.id).where(Foto.id == foto_id, condicao_elegivel(db, usuario_id, rodada))
    return db.scalar(consulta) is not None


COLUNAS_EXPORT = ["foto_id", "nome_arquivo", "lote", "n_rotuladores", *CLASSES, "empate"]


def exportar_csv(db: Session, rodadas: list[RodadaRotulagem]) -> tuple[str, int]:
    """Uma linha por foto, rotulo por maioria; empate fica vazio (NA no pandas)."""
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
    for c in sorted(consolidados, key=lambda c: fotos[c.foto][0]):
        nome, lote = fotos[c.foto]
        linha = {"foto_id": c.foto, "nome_arquivo": nome, "lote": lote}
        linha |= {"n_rotuladores": c.n_rotuladores, "empate": int(c.empate)}
        linha |= {k: "" if v is None else v for k, v in c.valores.items()}
        escritor.writerow(linha)
    return saida.getvalue(), len(consolidados)

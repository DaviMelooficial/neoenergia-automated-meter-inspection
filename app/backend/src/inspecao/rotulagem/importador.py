"""Importa os rotulos do Lab 2 (`lab-02/rotulos/rotulos.csv`) na rodada `lab02`.

Idempotente: a chave e (foto, rodada, rotulador externo). Rodar de novo nao
duplica nada; uma linha cujo valor mudou no CSV e atualizada, e a contagem diz
quantas. A foto e encontrada pelo nome do arquivo (`fotos.nome_arquivo`, unico);
nome que nao existe no banco e contado como nao mapeado e fica de fora.

O `gravado_em` do app e hora local sem fuso; e lido como America/Recife.

`controle`: com o `rotulador/controle.dat` (sha256 dos nomes das 50 fotos de
controle, fora do git para o rotulador nao saber quais sao), marca cada rotulo
lab02 como de controle ou nao. Sem o arquivo, o campo nao e tocado.
"""

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.enums import Cena, RodadaRotulagem
from inspecao.models import Foto, Rotulo
from inspecao.rotulagem.consolidacao import DEFEITOS

FUSO_APP = ZoneInfo("America/Recife")
COLUNAS_OBRIGATORIAS = {"nome_arquivo", "rotulador", "cena", *DEFEITOS, "legivel", "pulada"}
CAMPOS = ("cena", *DEFEITOS, "legivel", "segundos_na_foto", "gravado_em")


class CsvInvalido(ValueError):
    pass


@dataclass
class ResumoImportacao:
    linhas: int = 0
    puladas: int = 0
    inseridos: int = 0
    atualizados: int = 0
    inalterados: int = 0
    fotos_csv: int = 0
    fotos_mapeadas: int = 0
    nao_mapeadas: list[str] = field(default_factory=list)
    fotos_controle: int | None = None  # None = controle.dat nao informado

    @property
    def rotulos_no_banco(self) -> int:
        return self.inseridos + self.atualizados + self.inalterados


def _binario(valor: str, coluna: str, n: int) -> bool:
    if valor not in ("0", "1"):
        raise CsvInvalido(f"linha {n}: {coluna}={valor!r}, esperado 0 ou 1")
    return valor == "1"


def _ler(caminho: Path) -> tuple[list[tuple[int, dict]], int]:
    linhas, puladas, vistos = [], 0, set()
    with caminho.open(encoding="utf-8", newline="") as f:
        leitor = csv.DictReader(f)
        faltando = COLUNAS_OBRIGATORIAS - set(leitor.fieldnames or [])
        if faltando:
            raise CsvInvalido(f"colunas ausentes: {', '.join(sorted(faltando))}")
        for n, r in enumerate(leitor, start=2):
            if r["pulada"] == "1":
                puladas += 1
                continue
            chave = (r["nome_arquivo"], r["rotulador"])
            if chave in vistos:
                raise CsvInvalido(f"linha {n}: {chave[1]} rotulou {chave[0]} duas vezes")
            vistos.add(chave)
            if r["cena"] not in {c.value for c in Cena}:
                raise CsvInvalido(f"linha {n}: cena {r['cena']!r} fora do esquema")
            if not r["rotulador"].strip():
                raise CsvInvalido(f"linha {n}: rotulador vazio")
            valores = {
                "cena": r["cena"],
                "legivel": _binario(r["legivel"], "legivel", n),
                "segundos_na_foto": float(r["segundos_na_foto"])
                if r.get("segundos_na_foto")
                else None,
                "gravado_em": datetime.fromisoformat(r["gravado_em"]).replace(tzinfo=FUSO_APP)
                if r.get("gravado_em")
                else None,
            }
            for d in DEFEITOS:
                valores[d] = _binario(r[d], d, n)
            linhas.append(
                (n, {"nome_arquivo": r["nome_arquivo"], "rotulador": r["rotulador"]} | valores)
            )
    return linhas, puladas


def ler_controle(caminho: Path) -> set[str]:
    hashes = {h.strip() for h in caminho.read_text(encoding="ascii").splitlines() if h.strip()}
    invalidos = [h for h in hashes if len(h) != 64]
    if invalidos:
        raise CsvInvalido(f"controle.dat com linha que nao e sha256: {invalidos[0][:20]!r}")
    return hashes


def eh_controle(nome_arquivo: str, hashes: set[str]) -> bool:
    return hashlib.sha256(nome_arquivo.encode("utf-8")).hexdigest() in hashes


def importar(sessao: Session, caminho: Path, controle: set[str] | None = None) -> ResumoImportacao:
    """Grava na sessao (sem commit): quem chama decide se confirma."""
    linhas, puladas = _ler(caminho)
    resumo = ResumoImportacao(linhas=len(linhas) + puladas, puladas=puladas)
    nomes = sorted({r["nome_arquivo"] for _, r in linhas})
    resumo.fotos_csv = len(nomes)
    foto_por_nome = dict(
        sessao.execute(select(Foto.nome_arquivo, Foto.id).where(Foto.nome_arquivo.in_(nomes)))
        .tuples()
        .all()
    )
    resumo.fotos_mapeadas = len(foto_por_nome)
    resumo.nao_mapeadas = [n for n in nomes if n not in foto_por_nome]
    if controle is not None:
        resumo.fotos_controle = sum(1 for n in foto_por_nome if eh_controle(n, controle))

    existentes = {
        (r.foto_id, r.rotulador_externo): r
        for r in sessao.scalars(select(Rotulo).where(Rotulo.rodada == RodadaRotulagem.LAB02.value))
    }
    for _, r in linhas:
        foto_id = foto_por_nome.get(r["nome_arquivo"])
        if foto_id is None:
            continue
        valores = {k: r[k] for k in CAMPOS if not (k == "gravado_em" and r[k] is None)}
        if controle is not None:
            valores["controle"] = eh_controle(r["nome_arquivo"], controle)
        atual = existentes.get((foto_id, r["rotulador"]))
        if atual is None:
            sessao.add(
                Rotulo(
                    foto_id=foto_id,
                    rotulador_externo=r["rotulador"],
                    rodada=RodadaRotulagem.LAB02.value,
                    **valores,
                )
            )
            resumo.inseridos += 1
        elif any(getattr(atual, k) != v for k, v in valores.items()):
            for k, v in valores.items():
                setattr(atual, k, v)
            resumo.atualizados += 1
        else:
            resumo.inalterados += 1
    sessao.flush()
    return resumo

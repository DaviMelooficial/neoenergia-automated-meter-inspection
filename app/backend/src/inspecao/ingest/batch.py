"""Ingestao de um lote a partir de pasta em disco.

Uso:
    python -m inspecao.ingest.batch --lote PSP_EXTRATLEITIMPL_200526_0352
    python -m inspecao.ingest.batch --todos [--reprocessar]
"""

import argparse
import hashlib
import logging
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import IO

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from inspecao.config import get_settings
from inspecao.db import SessionLocal
from inspecao.enums import Ator
from inspecao.estados import ESTADO_INICIAL, registrar_estado_inicial
from inspecao.ingest import notas as notas_mod
from inspecao.ingest.parser import (
    PADRAO_LOTE,
    analisar_nome_foto,
    dimensoes_jpeg,
    ler_csv,
)
from inspecao.ingest.validacao import ResumoValidacao, validar_lote
from inspecao.models import Foto, Leitura, LeituraMedidor, Lote
from inspecao.storage.local import LocalFileStorage

log = logging.getLogger(__name__)

LOTE_INSERCAO = 2000


class BatchFolderSource:
    """Le lotes de um diretorio de staging."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz

    def _pasta(self, lote: str) -> Path:
        pasta = self.raiz / lote
        if not pasta.is_dir():
            raise FileNotFoundError(f"lote nao encontrado em {self.raiz}: {lote}")
        return pasta

    def lotes_disponiveis(self) -> list[str]:
        if not self.raiz.is_dir():
            return []
        return sorted(
            p.name for p in self.raiz.iterdir() if p.is_dir() and PADRAO_LOTE.match(p.name)
        )

    def caminho_csv(self, lote: str) -> Path:
        arquivos = sorted(self._pasta(lote).glob("*.csv"))
        if not arquivos:
            raise FileNotFoundError(f"nenhum CSV no lote {lote}")
        return arquivos[0]

    def nome_csv(self, lote: str) -> str:
        return self.caminho_csv(lote).name

    def abrir_csv(self, lote: str) -> IO[str]:
        # newline="" para o modulo csv tratar o CRLF; errors="replace" porque um
        # lote futuro pode trazer acento fora de ASCII.
        return self.caminho_csv(lote).open(encoding="utf-8", errors="replace", newline="")

    def listar_fotos(self, lote: str) -> Iterator[tuple[str, bytes]]:
        for caminho in sorted(self._pasta(lote).glob("*.jpg")):
            yield caminho.name, caminho.read_bytes()


@dataclass(slots=True)
class ResultadoIngestao:
    lote: str
    leituras: int = 0
    medidores: int = 0
    fotos: int = 0
    fotos_orfas: int = 0
    fotos_ausentes: list[str] = field(default_factory=list)
    validacao: ResumoValidacao = field(default_factory=ResumoValidacao)
    segundos: float = 0.0
    pulado: bool = False


def _checksum(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _data_do_lote(nome: str) -> tuple[date | None, str | None]:
    m = PADRAO_LOTE.match(nome)
    if not m:
        return None, None
    bruto = m.group("data")  # DDMMAA
    try:
        d = date(2000 + int(bruto[4:6]), int(bruto[2:4]), int(bruto[0:2]))
    except ValueError:
        d = None
    return d, m.group("hora")


def ingerir(
    sessao: Session,
    origem: BatchFolderSource,
    lote_nome: str,
    storage: LocalFileStorage,
    reprocessar: bool = False,
) -> ResultadoIngestao:
    inicio = time.perf_counter()
    resultado = ResultadoIngestao(lote=lote_nome)

    caminho_csv = origem.caminho_csv(lote_nome)
    checksum = _checksum(caminho_csv)

    existente = sessao.scalar(select(Lote).where(Lote.nome == lote_nome))
    if existente is not None:
        if existente.checksum_csv == checksum and not reprocessar:
            log.info("lote %s ja ingerido com o mesmo checksum, nada a fazer", lote_nome)
            resultado.pulado = True
            return resultado
        if not reprocessar:
            raise RuntimeError(
                f"lote {lote_nome} ja existe com checksum diferente. "
                "Use --reprocessar para substituir."
            )
        log.warning("reprocessando lote %s: apagando dados anteriores", lote_nome)
        sessao.execute(delete(Lote).where(Lote.id == existente.id))
        sessao.flush()

    data_extracao, hora_extracao = _data_do_lote(lote_nome)
    lote = Lote(
        nome=lote_nome,
        data_extracao=data_extracao,
        hora_extracao=hora_extracao,
        csv_nome=origem.nome_csv(lote_nome),
        checksum_csv=checksum,
    )
    sessao.add(lote)
    sessao.flush()  # garante lote.id

    # ---------------------------------------------------------------- leituras
    linhas = []
    with origem.abrir_csv(lote_nome) as fh:
        linhas = list(ler_csv(fh))

    sessao.bulk_insert_mappings(
        Leitura,
        [
            {
                "lote_id": lote.id,
                "linha_csv": ln.linha_csv,
                "posicao_lida": ln.posicao_lida,
                "nota_codigo": ln.nota_codigo,
                "status_atual": ESTADO_INICIAL.value,  # unico ponto fora de estados.py
            }
            for ln in linhas
        ],
    )
    sessao.flush()
    resultado.leituras = len(linhas)

    # Mapeia linha_csv -> id, para ligar medidores e fotos sem um SELECT por linha.
    id_por_linha = dict(
        sessao.execute(
            select(Leitura.linha_csv, Leitura.id).where(Leitura.lote_id == lote.id)
        ).all()
    )

    medidores = [
        {"leitura_id": id_por_linha[ln.linha_csv], "numero": numero, "ordem": ordem}
        for ln in linhas
        for ordem, numero in enumerate(ln.medidores)
    ]
    for i in range(0, len(medidores), LOTE_INSERCAO):
        sessao.bulk_insert_mappings(LeituraMedidor, medidores[i : i + LOTE_INSERCAO])
    resultado.medidores = len(medidores)

    # ------------------------------------------------------------------- fotos
    # Nome de arquivo -> id da leitura que o referencia no CSV. Se duas linhas
    # apontarem o mesmo arquivo, a PRIMEIRA fica com a foto; a outra e tratada
    # pela validacao como referencia nao atendida (foto_referenciada_ausente).
    leitura_por_foto: dict[str, int] = {}
    for ln in linhas:
        if ln.foto_nome:
            leitura_por_foto.setdefault(ln.foto_nome, id_por_linha[ln.linha_csv])
    vistos: set[str] = set()
    pendentes: list[dict] = []

    for nome, dados in origem.listar_fotos(lote_nome):
        vistos.add(nome)
        partes = analisar_nome_foto(nome)
        largura, altura = dimensoes_jpeg(dados)
        chave = f"{lote_nome}/{nome}"
        storage.salvar(chave, dados)

        leitura_id = leitura_por_foto.get(nome)
        if leitura_id is None:
            resultado.fotos_orfas += 1

        pendentes.append(
            {
                "lote_id": lote.id,
                "leitura_id": leitura_id,
                "principal": leitura_id is not None,
                "nome_arquivo": nome,
                "id_externo": partes.id_externo if partes else "",
                "sequencia": partes.sequencia if partes else -1,
                "caminho_storage": chave,
                "largura": largura,
                "altura": altura,
                "bytes": len(dados),
                "sha256": hashlib.sha256(dados).hexdigest(),
            }
        )
        if len(pendentes) >= LOTE_INSERCAO:
            sessao.bulk_insert_mappings(Foto, pendentes)
            pendentes.clear()

    if pendentes:
        sessao.bulk_insert_mappings(Foto, pendentes)

    resultado.fotos = len(vistos)
    resultado.fotos_ausentes = sorted(set(leitura_por_foto) - vistos)

    # ------------------------------------------------- historico e validacao
    registrar_estado_inicial(sessao, id_por_linha.values(), Ator.INGESTAO.value)
    resultado.validacao = validar_lote(sessao, lote.id, linhas, id_por_linha, vistos)

    lote.total_linhas = resultado.leituras
    lote.total_fotos_disco = resultado.fotos
    sessao.flush()

    resultado.segundos = time.perf_counter() - inicio
    return resultado


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description="Ingere lotes de fotos e leituras")
    ap.add_argument("--lote", help="nome do lote em data/staging")
    ap.add_argument("--todos", action="store_true", help="ingere todos os lotes disponiveis")
    ap.add_argument("--reprocessar", action="store_true", help="substitui lote ja ingerido")
    args = ap.parse_args(argv)

    if not args.lote and not args.todos:
        ap.error("informe --lote NOME ou --todos")

    settings = get_settings()
    origem = BatchFolderSource(settings.caminho_absoluto(settings.ingest_staging_dir))
    storage = LocalFileStorage()

    alvos = origem.lotes_disponiveis() if args.todos else [args.lote]
    if not alvos:
        log.error("nenhum lote em %s. Rode: make seed-staging LOTE=...", origem.raiz)
        return 1

    total = time.perf_counter()
    with SessionLocal() as sessao:
        notas_mod.sincronizar(sessao, settings.caminho_absoluto(settings.ingest_notas_xlsx))
        sessao.commit()

        for nome in alvos:
            try:
                r = ingerir(sessao, origem, nome, storage, reprocessar=args.reprocessar)
                sessao.commit()
            except Exception:
                sessao.rollback()
                log.exception("falha ao ingerir %s", nome)
                return 1

            if r.pulado:
                log.info("%s: ja ingerido, pulado", nome)
                continue
            log.info(
                "%s: %d leituras, %d medidores, %d fotos (%d orfas) em %.1fs",
                nome, r.leituras, r.medidores, r.fotos, r.fotos_orfas, r.segundos,
            )
            v = r.validacao
            log.info(
                "%s: %d sem_foto, %d seguem para triagem, %d bloqueantes; quarentena: %s",
                nome, v.sem_foto, v.seguem_para_triagem, v.bloqueantes, v.quarentena_por_tipo,
            )
            if r.fotos_ausentes:
                log.warning(
                    "%s: %d fotos referenciadas no CSV ausentes do disco",
                    nome, len(r.fotos_ausentes),
                )

    log.info("tempo total: %.1fs", time.perf_counter() - total)
    return 0


if __name__ == "__main__":
    sys.exit(main())

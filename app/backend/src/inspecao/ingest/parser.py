"""Parsing do CSV de extracao e do nome de arquivo das fotos.

As regras aqui foram verificadas nos 4 lotes reais de "Base cliente/".
Ver o doc 02 do plano de desenvolvimento, secoes 1 e 2.
"""

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO

# O CSV usa a string literal "NA" para campo vazio. Nao e ausencia de valor.
VAZIOS = frozenset({"", "NA"})

CABECALHO_ESPERADO = [
    "Numero do medidor",
    "Posicao do medidor lida",
    "Nota de Leitura Atual",
    "Foto do medidor",
]

PADRAO_FOTO = re.compile(
    r"^PSP_EXTRATLEITIMPL_(?P<data>\d{6})_(?P<hora>\d{4})_(?P<id>\d{20})_(?P<seq>\d{3})\.jpg$"
)

PADRAO_LOTE = re.compile(r"^PSP_EXTRATLEITIMPL_(?P<data>\d{6})_(?P<hora>\d{4})$")


class CabecalhoInesperado(ValueError):
    """O CSV nao tem o cabecalho do contrato. Falha a ingestao cedo."""


def limpar(valor: str | None) -> str | None:
    """Normaliza campo do CSV. Converte "" e "NA" em None."""
    v = (valor or "").strip()
    return None if v in VAZIOS else v


def dividir_medidores(bruto: str | None) -> list[str]:
    """Separa a coluna de medidor por barra.

    Chega a 8 medidores em uma linha e aceita alfanumerico (ex.: F00001),
    por isso o retorno e sempre lista de texto, nunca inteiro.
    """
    valor = limpar(bruto)
    if valor is None:
        return []
    return [p.strip() for p in valor.split("/") if p.strip()]


def converter_posicao(bruto: str | None) -> tuple[int | None, bool]:
    """Converte a posicao lida em inteiro.

    Devolve (valor, invalido). `invalido` marca o caso em que havia conteudo
    mas ele nao era numerico, que vira quarentena no marco M2.
    """
    valor = limpar(bruto)
    if valor is None:
        return None, False
    try:
        return int(valor), False
    except ValueError:
        return None, True


@dataclass(slots=True)
class LinhaLeitura:
    linha_csv: int
    medidores: list[str] = field(default_factory=list)
    posicao_lida: int | None = None
    posicao_invalida: bool = False
    nota_codigo: str | None = None
    foto_nome: str | None = None


def ler_csv(fh: IO[str]) -> Iterator[LinhaLeitura]:
    """Le o CSV de extracao e devolve as linhas ja normalizadas.

    O arquivo deve ser aberto com newline="" para que o modulo csv trate o
    CRLF corretamente.
    """
    leitor = csv.DictReader(fh, delimiter=";")

    campos = [c.strip().lstrip("﻿") for c in (leitor.fieldnames or [])]
    if campos != CABECALHO_ESPERADO:
        raise CabecalhoInesperado(f"esperado {CABECALHO_ESPERADO}, encontrado {campos}")

    for numero, linha in enumerate(leitor, start=2):  # linha 1 e o cabecalho
        posicao, invalida = converter_posicao(linha.get("Posicao do medidor lida"))
        yield LinhaLeitura(
            linha_csv=numero,
            medidores=dividir_medidores(linha.get("Numero do medidor")),
            posicao_lida=posicao,
            posicao_invalida=invalida,
            nota_codigo=limpar(linha.get("Nota de Leitura Atual")),
            foto_nome=limpar(linha.get("Foto do medidor")),
        )


@dataclass(slots=True)
class NomeFoto:
    data: str
    hora: str
    id_externo: str
    sequencia: int


def analisar_nome_foto(nome: str) -> NomeFoto | None:
    """Extrai as partes do nome do arquivo. None se nao casar com o padrao."""
    m = PADRAO_FOTO.match(nome)
    if m is None:
        return None
    return NomeFoto(
        data=m.group("data"),
        hora=m.group("hora"),
        id_externo=m.group("id"),
        sequencia=int(m.group("seq")),
    )


def dimensoes_jpeg(dados: bytes) -> tuple[int | None, int | None]:
    """Le largura e altura direto do cabecalho JPEG.

    Nao decodifica a imagem de proposito: com 12.340 arquivos por ingestao,
    decodificar cada um custaria caro demais para o orcamento de 90 segundos.
    Percorre os marcadores ate achar um SOF.
    """
    if not dados.startswith(b"\xff\xd8"):
        return None, None

    i, n = 2, len(dados)
    while i < n - 9:
        if dados[i] != 0xFF:
            i += 1
            continue
        marcador = dados[i + 1]
        # Marcadores sem payload
        if marcador in (0xD8, 0x01) or 0xD0 <= marcador <= 0xD7:
            i += 2
            continue
        tamanho = int.from_bytes(dados[i + 2 : i + 4], "big")
        # SOF0 a SOF15, exceto DHT (C4), JPG (C8) e DAC (CC)
        if 0xC0 <= marcador <= 0xCF and marcador not in (0xC4, 0xC8, 0xCC):
            altura = int.from_bytes(dados[i + 5 : i + 7], "big")
            largura = int.from_bytes(dados[i + 7 : i + 9], "big")
            return largura, altura
        if tamanho <= 0:
            break
        i += 2 + tamanho

    return None, None

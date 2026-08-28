"""Contrato de origem de lotes.

Hoje a origem e uma pasta em disco (BatchFolderSource), porque o app de campo
e de terceiros e nao ha API confirmada. Se um webhook for negociado, basta uma
nova implementacao deste Protocol.
"""

from collections.abc import Iterator
from typing import IO, Protocol, runtime_checkable


@runtime_checkable
class IngestSource(Protocol):
    def lotes_disponiveis(self) -> list[str]:
        """Nomes de lote prontos para ingestao."""
        ...

    def abrir_csv(self, lote: str) -> IO[str]:
        """Abre o CSV de extracao do lote."""
        ...

    def nome_csv(self, lote: str) -> str: ...

    def listar_fotos(self, lote: str) -> Iterator[tuple[str, bytes]]:
        """Itera (nome_arquivo, conteudo) das fotos do lote."""
        ...

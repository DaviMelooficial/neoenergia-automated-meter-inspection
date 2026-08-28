"""Contrato de armazenamento de fotos.

O sistema roda local (LocalFileStorage). Um backend S3/MinIO futuro implementa
este mesmo Protocol sem exigir mudanca em nenhum chamador.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    def salvar(self, chave: str, dados: bytes) -> str:
        """Grava os bytes sob a chave e devolve o identificador persistido."""
        ...

    def abrir(self, chave: str) -> bytes:
        """Le os bytes da chave. Levanta FileNotFoundError se nao existir."""
        ...

    def existe(self, chave: str) -> bool: ...

    def remover(self, chave: str) -> None: ...

    def verificar_saude(self) -> bool:
        """Escreve e le um arquivo temporario. Usado por /health."""
        ...

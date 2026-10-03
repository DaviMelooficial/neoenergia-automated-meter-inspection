"""Armazenamento em sistema de arquivos local. Sem nuvem."""

import uuid
from pathlib import Path

from inspecao.config import get_settings


class LocalFileStorage:
    """Implementa StorageBackend gravando sob STORAGE_LOCAL_ROOT."""

    def __init__(self, raiz: Path | None = None) -> None:
        settings = get_settings()
        # resolve() tambem na raiz: em macOS /tmp e symlink de /private/tmp e a
        # comparacao com o destino resolvido falharia sem isso.
        self.raiz = (raiz or settings.caminho_absoluto(settings.storage_local_root)).resolve()
        self.raiz.mkdir(parents=True, exist_ok=True)

    def _caminho(self, chave: str) -> Path:
        destino = (self.raiz / chave).resolve()
        # Impede que uma chave com ".." escape da raiz do storage.
        if not destino.is_relative_to(self.raiz):
            raise ValueError(f"Chave invalida, escapa da raiz do storage: {chave!r}")
        return destino

    def salvar(self, chave: str, dados: bytes) -> str:
        destino = self._caminho(chave)
        destino.parent.mkdir(parents=True, exist_ok=True)
        # Grava em temporario e renomeia: evita arquivo truncado se o processo morrer.
        temporario = destino.with_suffix(destino.suffix + f".tmp{uuid.uuid4().hex[:8]}")
        temporario.write_bytes(dados)
        temporario.replace(destino)
        return chave

    def abrir(self, chave: str) -> bytes:
        return self._caminho(chave).read_bytes()

    def existe(self, chave: str) -> bool:
        return self._caminho(chave).is_file()

    def remover(self, chave: str) -> None:
        self._caminho(chave).unlink(missing_ok=True)

    def verificar_saude(self) -> bool:
        chave = f".health/{uuid.uuid4().hex}"
        try:
            self.salvar(chave, b"ok")
            resultado = self.abrir(chave) == b"ok"
            self.remover(chave)
            return resultado
        except Exception:
            return False

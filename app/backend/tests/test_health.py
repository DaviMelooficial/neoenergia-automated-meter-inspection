"""Teste de fumaca do marco M0: a aplicacao sobe e /health responde."""

from fastapi.testclient import TestClient

from inspecao.main import app

cliente = TestClient(app)


def test_health_responde_200_com_os_tres_componentes():
    resposta = cliente.get("/health")
    assert resposta.status_code == 200

    corpo = resposta.json()
    assert corpo["status"] in {"ok", "degradado"}
    assert set(corpo["componentes"]) == {"banco", "storage", "modelo_qualidade"}


def test_storage_local_escreve_e_le():
    from inspecao.storage.local import LocalFileStorage

    storage = LocalFileStorage()
    assert storage.verificar_saude() is True


def test_storage_recusa_chave_que_escapa_da_raiz():
    import pytest

    from inspecao.storage.local import LocalFileStorage

    storage = LocalFileStorage()
    with pytest.raises(ValueError, match="escapa da raiz"):
        storage.salvar("../fora.txt", b"x")


def test_raiz_resolve_sem_depender_da_profundidade_do_caminho(monkeypatch, tmp_path):
    """Regressao: o container instala o codigo em /app/src/inspecao, que tem
    menos niveis que o layout do host. Contar parents fixos quebrava ali."""
    from inspecao.config import _descobrir_raiz

    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    assert _descobrir_raiz() == tmp_path.resolve()

    monkeypatch.delenv("APP_ROOT")
    # Sem override, precisa achar a raiz por marcador e nao estourar IndexError.
    assert _descobrir_raiz().is_dir()

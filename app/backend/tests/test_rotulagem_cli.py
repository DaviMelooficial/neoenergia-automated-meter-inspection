"""CLI dos importadores de rotulos (`inspecao.rotulagem.cli`): codigo de saida e avisos.

CSV ausente ou invalido sai com 1 e uma linha de ERROR, sem traceback. Foto da inspecao que
nao esta no banco nao derruba a importacao, mas loga WARNING de importacao parcial: ela fica
sem rotulo e fora da exclusao da calibracao. Os nomes usados aqui nao existem em nenhum
banco, entao a importacao que roda de verdade (commit no banco de teste) nao grava nada.
"""

import logging

import pytest

from inspecao.rotulagem import cli


@pytest.fixture
def banco(db_session, monkeypatch):
    """So para pular sem servidor: o CLI abre a propria sessao (`SessionLocal`). O logger
    do CLI foi desligado pelo fileConfig do alembic na migracao do banco de teste."""
    monkeypatch.setattr(cli.log, "disabled", False)
    return db_session


@pytest.mark.parametrize(
    "comando", ["importar-lab02", "importar-inspecao-m5", "importar-inspecao-m7"]
)
def test_csv_ausente_sai_com_1_sem_traceback(banco, tmp_path, caplog, comando):
    ausente = tmp_path / "nao_existe.csv"
    with caplog.at_level(logging.ERROR):
        assert cli.main([comando, "--csv", str(ausente)]) == 1
    erros = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(erros) == 1 and "csv nao encontrado" in erros[0].getMessage()
    assert erros[0].exc_info is None


@pytest.mark.parametrize("comando", ["importar-inspecao-m5", "importar-inspecao-m7"])
def test_csv_de_inspecao_invalido_sai_com_1(banco, tmp_path, caplog, comando):
    csv = tmp_path / "inspecao.csv"
    csv.write_text("arquivo,lote\nx.jpg,L\n", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert cli.main([comando, "--csv", str(csv)]) == 1
    assert "csv invalido" in caplog.text and "colunas ausentes" in caplog.text


@pytest.mark.parametrize(
    ("comando", "rodada"),
    [("importar-inspecao-m5", "inspecao_m5"), ("importar-inspecao-m7", "inspecao_m7")],
)
def test_importacao_parcial_avisa(banco, tmp_path, caplog, capsys, comando, rodada):
    csv = tmp_path / "inspecao.csv"
    csv.write_text(
        "arquivo,lote,legivel_inspecao,inspetor\n"
        "PSP_EXTRATLEITIMPL_990000_0000_00000000000000000001_000.jpg,L,1,ana\n"
        "PSP_EXTRATLEITIMPL_990000_0000_00000000000000000002_000.jpg,L,,\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert cli.main([comando, "--csv", str(csv)]) == 0
    avisos = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(avisos) == 1 and "importacao parcial: 2 fotos" in avisos[0]
    saida = capsys.readouterr().out
    assert f"rotulos {rodada} 0 (0 inspecionadas, 0 pendentes)" in saida
    assert "nao mapeadas        2" in saida

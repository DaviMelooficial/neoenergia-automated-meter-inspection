"""Testes do parser. Cobrem as armadilhas reais encontradas nos 4 lotes."""

import io

import pytest

from inspecao.ingest.parser import (
    CABECALHO_ESPERADO,
    CabecalhoInesperado,
    analisar_nome_foto,
    converter_posicao,
    dimensoes_jpeg,
    dividir_medidores,
    ler_csv,
    limpar,
)

CABECALHO = ";".join(CABECALHO_ESPERADO)


def montar_csv(*linhas: str) -> io.StringIO:
    return io.StringIO(CABECALHO + "\r\n" + "\r\n".join(linhas) + "\r\n", newline="")


# --------------------------------------------------------------------- limpar


@pytest.mark.parametrize("bruto", ["NA", "", "   ", "  NA  "])
def test_na_e_vazio_viram_none(bruto):
    """A armadilha central: "NA" e string literal, nao ausencia de valor."""
    assert limpar(bruto) is None


def test_valor_real_sobrevive():
    assert limpar("  T181 ") == "T181"
    # "NAO" comeca com NA mas nao e sentinela
    assert limpar("NAO") == "NAO"


# ----------------------------------------------------------------- medidores


def test_medidor_simples():
    assert dividir_medidores("3231111165") == ["3231111165"]


def test_medidor_alfanumerico_nao_vira_int():
    medidores = dividir_medidores("5256269114/F50081")
    assert medidores == ["5256269114", "F50081"]
    assert all(isinstance(m, str) for m in medidores)


def test_ate_oito_medidores():
    bruto = "/".join(str(i) for i in range(1, 9))
    assert len(dividir_medidores(bruto)) == 8


def test_medidor_na_vira_lista_vazia():
    assert dividir_medidores("NA") == []
    assert dividir_medidores(None) == []


def test_barras_vazias_sao_descartadas():
    assert dividir_medidores("111//222/") == ["111", "222"]


# ------------------------------------------------------------------- posicao


def test_posicao_numerica():
    assert converter_posicao("17413") == (17413, False)


def test_posicao_na_nao_e_invalida():
    """Ausencia de posicao e normal, nao vira quarentena."""
    assert converter_posicao("NA") == (None, False)


def test_posicao_nao_numerica_e_sinalizada():
    valor, invalida = converter_posicao("abc")
    assert valor is None and invalida is True


# ----------------------------------------------------------------------- CSV


def test_le_linhas_e_numera_a_partir_de_dois():
    csv = montar_csv(
        "3231111165;29;NA;PSP_EXTRATLEITIMPL_200526_0352_20000000007102969765_001.jpg",
        "3245112423/3223285413;5140;T181;NA",
    )
    linhas = list(ler_csv(csv))

    assert len(linhas) == 2
    assert linhas[0].linha_csv == 2
    assert linhas[0].nota_codigo is None
    assert linhas[0].foto_nome.endswith("_001.jpg")

    assert linhas[1].linha_csv == 3
    assert linhas[1].nota_codigo == "T181"
    assert linhas[1].foto_nome is None
    assert linhas[1].medidores == ["3245112423", "3223285413"]


def test_cabecalho_diferente_falha_cedo():
    ruim = io.StringIO("a;b;c\r\n1;2;3\r\n", newline="")
    with pytest.raises(CabecalhoInesperado):
        list(ler_csv(ruim))


def test_bom_utf8_no_cabecalho_e_tolerado():
    csv = io.StringIO("﻿" + CABECALHO + "\r\n111;1;NA;NA\r\n", newline="")
    assert len(list(ler_csv(csv))) == 1


# --------------------------------------------------------------- nome de foto


def test_nome_de_foto_valido():
    p = analisar_nome_foto("PSP_EXTRATLEITIMPL_200526_0352_20000000007102969765_001.jpg")
    assert p is not None
    assert p.data == "200526"
    assert p.hora == "0352"
    assert p.id_externo == "20000000007102969765"
    assert p.sequencia == 1


def test_sequencia_vai_ate_oito():
    p = analisar_nome_foto("PSP_EXTRATLEITIMPL_030726_0121_00000000000211734196_008.jpg")
    assert p is not None and p.sequencia == 8


@pytest.mark.parametrize(
    "nome",
    [
        "outra_coisa.jpg",
        "PSP_EXTRATLEITIMPL_200526_0352_123_001.jpg",  # id curto demais
        "PSP_EXTRATLEITIMPL_200526_0352_20000000007102969765_001.png",
    ],
)
def test_nome_fora_do_padrao(nome):
    assert analisar_nome_foto(nome) is None


# ------------------------------------------------------------------- dimensao


def test_dimensoes_jpeg_le_cabecalho():
    # JPEG minimo: SOI + SOF0 declarando 480 de altura por 360 de largura
    jpeg = (
        b"\xff\xd8"
        b"\xff\xc0\x00\x11\x08"
        + (480).to_bytes(2, "big")
        + (360).to_bytes(2, "big")
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    assert dimensoes_jpeg(jpeg) == (360, 480)


def test_dimensoes_de_nao_jpeg():
    assert dimensoes_jpeg(b"nao sou jpeg") == (None, None)

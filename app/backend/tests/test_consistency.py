"""Regra deterministica de consistencia: testavel sem banco e sem imagem."""

import pytest

from inspecao.enums import Severidade, StatusLeitura, TipoInconsistencia
from inspecao.inference.consistency import NOTAS_PRESSUPOEM_LEITURA, avaliar


def tipos(veredito):
    return [a.tipo for a in veredito.achados]


def test_sem_nota_com_foto_segue_limpa_para_triagem():
    v = avaliar(nota_codigo=None, nota_conhecida=False, nota_exige_foto=False, tem_foto=True)
    assert v.segue_para_triagem
    assert v.achados == ()


def test_sem_nota_sem_foto_vai_para_sem_foto_sem_quarentena():
    v = avaliar(nota_codigo=None, nota_conhecida=False, nota_exige_foto=False, tem_foto=False)
    assert v.novo_status is StatusLeitura.SEM_FOTO
    assert v.achados == ()
    assert not v.bloqueante


def test_nota_exige_foto_e_nao_tem_e_bloqueante():
    v = avaliar(nota_codigo="T181", nota_conhecida=True, nota_exige_foto=True, tem_foto=False)
    assert v.novo_status is StatusLeitura.SEM_FOTO
    assert tipos(v) == [TipoInconsistencia.NOTA_EXIGE_SEM_FOTO]
    assert v.bloqueante
    assert v.achados[0].detalhe == {"nota_codigo": "T181"}


def test_nota_nao_exige_mas_tem_foto_e_informativa_e_segue():
    v = avaliar(
        nota_codigo="T111",
        nota_conhecida=True,
        nota_exige_foto=False,
        tem_foto=True,
        posicao_lida=12345,
    )
    assert v.segue_para_triagem
    assert tipos(v) == [TipoInconsistencia.FOTO_SEM_EXIGENCIA]
    assert v.achados[0].severidade is Severidade.INFORMATIVO


def test_nota_nao_exige_e_nao_tem_foto_e_o_caso_normal():
    v = avaliar(nota_codigo="L131", nota_conhecida=True, nota_exige_foto=False, tem_foto=False)
    assert v.novo_status is StatusLeitura.SEM_FOTO
    assert v.achados == ()


def test_codigo_nao_mapeado_e_informativo_e_nao_acusa_falta_de_foto():
    """V100 nao esta na planilha: sem saber se exige foto, nao pode virar bloqueante."""
    v = avaliar(nota_codigo="V100", nota_conhecida=False, nota_exige_foto=True, tem_foto=False)
    assert tipos(v) == [TipoInconsistencia.CODIGO_NAO_MAPEADO]
    assert not v.bloqueante
    assert v.novo_status is StatusLeitura.SEM_FOTO


def test_codigo_nao_mapeado_com_foto_segue_para_triagem():
    v = avaliar(nota_codigo="V100", nota_conhecida=False, nota_exige_foto=False, tem_foto=True)
    assert v.segue_para_triagem
    assert tipos(v) == [TipoInconsistencia.CODIGO_NAO_MAPEADO]


def test_foto_referenciada_ausente_e_bloqueante_e_nao_vai_a_triagem():
    v = avaliar(
        nota_codigo=None,
        nota_conhecida=False,
        nota_exige_foto=False,
        tem_foto=False,
        foto_referenciada_ausente=True,
    )
    assert v.novo_status is StatusLeitura.SEM_FOTO
    assert tipos(v) == [TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE]
    assert v.bloqueante


def test_foto_ausente_nao_duplica_com_nota_exige_sem_foto():
    """Um unico achado bloqueante: a causa raiz e a foto prometida que nao veio."""
    v = avaliar(
        nota_codigo="T181",
        nota_conhecida=True,
        nota_exige_foto=True,
        tem_foto=False,
        foto_referenciada_ausente=True,
    )
    assert tipos(v) == [TipoInconsistencia.FOTO_REFERENCIADA_AUSENTE]


@pytest.mark.parametrize("qtd", [2, 8])
def test_medidor_multiplo_e_informativo_com_quantidade(qtd):
    v = avaliar(
        nota_codigo=None,
        nota_conhecida=False,
        nota_exige_foto=False,
        tem_foto=True,
        quantidade_medidores=qtd,
    )
    assert tipos(v) == [TipoInconsistencia.MEDIDOR_MULTIPLO]
    assert v.achados[0].detalhe == {"quantidade": qtd}
    assert v.segue_para_triagem


def test_um_medidor_nao_gera_achado():
    v = avaliar(
        nota_codigo=None,
        nota_conhecida=False,
        nota_exige_foto=False,
        tem_foto=True,
        quantidade_medidores=1,
    )
    assert v.achados == ()


def test_posicao_invalida_e_informativa():
    v = avaliar(
        nota_codigo=None,
        nota_conhecida=False,
        nota_exige_foto=False,
        tem_foto=True,
        posicao_lida=None,
        posicao_invalida=True,
    )
    assert tipos(v) == [TipoInconsistencia.POSICAO_INVALIDA]


@pytest.mark.parametrize("nota", sorted(NOTAS_PRESSUPOEM_LEITURA))
def test_posicao_ausente_com_nota_que_pressupoe_leitura(nota):
    v = avaliar(
        nota_codigo=nota,
        nota_conhecida=True,
        nota_exige_foto=False,
        tem_foto=True,
        posicao_lida=None,
    )
    assert TipoInconsistencia.POSICAO_AUSENTE in tipos(v)


def test_posicao_ausente_nao_dispara_para_nota_comum():
    v = avaliar(
        nota_codigo="T181",
        nota_conhecida=True,
        nota_exige_foto=True,
        tem_foto=True,
        posicao_lida=None,
    )
    assert TipoInconsistencia.POSICAO_AUSENTE not in tipos(v)


def test_posicao_invalida_tem_precedencia_sobre_ausente():
    """Conteudo nao numerico ja explica a posicao nula; nao acusar duas vezes."""
    v = avaliar(
        nota_codigo="T111",
        nota_conhecida=True,
        nota_exige_foto=False,
        tem_foto=True,
        posicao_lida=None,
        posicao_invalida=True,
    )
    assert TipoInconsistencia.POSICAO_INVALIDA in tipos(v)
    assert TipoInconsistencia.POSICAO_AUSENTE not in tipos(v)


def test_achados_acumulam_em_uma_mesma_leitura():
    v = avaliar(
        nota_codigo="V100",
        nota_conhecida=False,
        nota_exige_foto=False,
        tem_foto=True,
        quantidade_medidores=3,
        posicao_invalida=True,
    )
    assert set(tipos(v)) == {
        TipoInconsistencia.CODIGO_NAO_MAPEADO,
        TipoInconsistencia.MEDIDOR_MULTIPLO,
        TipoInconsistencia.POSICAO_INVALIDA,
    }

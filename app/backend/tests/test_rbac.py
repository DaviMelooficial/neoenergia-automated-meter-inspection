"""Cada rota protegida contra cada papel. Aceite do M3: auditor recebe 403 nas
rotas de supervisor e admin; nao autenticado recebe 401 em todas."""

import pytest

from inspecao.enums import Papel
from tests.usuarios_fixture import login

# (metodo, rota, papel minimo, corpo)
ROTAS = [
    ("GET", "/lotes", Papel.AUDITOR, None),
    ("GET", "/leituras/1", Papel.AUDITOR, None),
    ("GET", "/fotos/1/arquivo", Papel.AUDITOR, None),
    ("GET", "/fila-humana", Papel.AUDITOR, None),
    ("POST", "/fila-humana/proxima", Papel.AUDITOR, None),
    ("POST", "/fila-humana/1/decisao", Papel.AUDITOR, {"decisao": "aprovada"}),
    ("POST", "/fila-humana/1/liberar", Papel.AUDITOR, None),
    ("GET", "/quarentena", Papel.SUPERVISOR, None),
    ("GET", "/quarentena/resumo", Papel.SUPERVISOR, None),
    ("POST", "/quarentena/1/resolver", Papel.SUPERVISOR, {}),
    ("GET", "/metricas/qualidade", Papel.SUPERVISOR, None),
    ("GET", "/metricas/drift", Papel.SUPERVISOR, None),
    ("GET", "/metricas/auto-aprovacao", Papel.SUPERVISOR, None),
    ("GET", "/metricas/auditoria", Papel.SUPERVISOR, None),
    ("POST", "/auditoria/proxima", Papel.AUDITOR, None),
    (
        "POST",
        "/auditoria/00000000-0000-4000-8000-000000000000/decisao",
        Papel.AUDITOR,
        {"decisao": "aprovada"},
    ),
    ("POST", "/auditoria/00000000-0000-4000-8000-000000000000/liberar", Papel.AUDITOR, None),
    ("POST", "/rotulagem/proxima", Papel.SUPERVISOR, None),
    (
        "POST",
        "/rotulagem/1",
        Papel.SUPERVISOR,
        {"rodada": "a", "cena": "digital", "legivel": True, "defeitos": []},
    ),
    ("GET", "/rotulagem/consolidacao", Papel.SUPERVISOR, None),
    ("GET", "/rotulagem/export", Papel.ADMIN, None),
    ("GET", "/usuarios", Papel.ADMIN, None),
    (
        "POST",
        "/usuarios",
        Papel.ADMIN,
        {"nome": "x", "email": "x@x.com.br", "senha_inicial": "1234567890ab"},
    ),
    ("PATCH", "/usuarios/1", Papel.ADMIN, {"ativo": True}),
    ("POST", "/ingest/batch", Papel.ADMIN, {"lote": "PSP_EXTRATLEITIMPL_010199_0000"}),
    ("POST", "/ingest/webhook", Papel.ADMIN, None),
]
ORDEM = {Papel.AUDITOR: 1, Papel.SUPERVISOR: 2, Papel.ADMIN: 3}
# Excecao a hierarquia: admin ve o kappa das rodadas cegas, entao nao rotula nelas.
ADMIN_BARRADO = {("POST", "/rotulagem/proxima"), ("POST", "/rotulagem/1")}
# Auditoria amostral (M8): so o papel auditor audita; supervisor e admin leem as metricas.
SO_AUDITOR = {
    ("POST", "/auditoria/proxima"),
    ("POST", "/auditoria/00000000-0000-4000-8000-000000000000/decisao"),
    ("POST", "/auditoria/00000000-0000-4000-8000-000000000000/liberar"),
}


def chamar(client, metodo, rota, corpo, headers):
    return client.request(metodo, rota, json=corpo, headers=headers)


@pytest.mark.parametrize(("metodo", "rota", "minimo", "corpo"), ROTAS)
def test_sem_token_da_401(client, contas, metodo, rota, minimo, corpo):
    assert chamar(client, metodo, rota, corpo, {}).status_code == 401


@pytest.mark.parametrize("papel", [Papel.AUDITOR, Papel.SUPERVISOR, Papel.ADMIN])
@pytest.mark.parametrize(("metodo", "rota", "minimo", "corpo"), ROTAS)
def test_papel_contra_rota(client, contas, papel, metodo, rota, minimo, corpo):
    cab = login(client, contas[papel.value])
    status = chamar(client, metodo, rota, corpo, cab).status_code
    barrado = (papel is Papel.ADMIN and (metodo, rota) in ADMIN_BARRADO) or (
        papel is not Papel.AUDITOR and (metodo, rota) in SO_AUDITOR
    )
    if ORDEM[papel] < ORDEM[minimo] or barrado:
        assert status == 403, (papel, metodo, rota, status)
    else:
        # Autorizado: a rota pode devolver 404/409/422/501 pelo estado vazio do banco,
        # mas nunca 401 nem 403.
        assert status not in (401, 403), (papel, metodo, rota, status)

"""Aceite do M8 nas metricas: /metricas/qualidade, /metricas/drift e
/metricas/auto-aprovacao com valores conhecidos, montados direto no banco."""

import math
from datetime import date

import pytest

from inspecao.config import get_settings
from inspecao.enums import StatusLeitura as S
from inspecao.monitoramento import auto_aprovacao, drift
from tests import m8_fixture as fx
from tests.usuarios_fixture import login


@pytest.fixture
def cenario(db_session, contas):
    """Lote A (maio) e B (julho), com p_ruim conhecido e decisoes de auditor.

    A: a1 rejeitada p=0,90 | a2 aprovada p=0,02 | a3 reprovada (fora_de_foco) p=0,60 |
       a4 fila p=0,10 | a5 sem foto
    B: b1 aprovada p=0,03 | b2 fila triada pelo nulo@v0 (sem score) |
       c1 aprovada p=0,04 por conta de TESTE (@teste.com.br): nao e medida
    """
    aud = fx.contas_reais(db_session)["auditor"].id
    a = fx.lote(db_session, "0526_0001", date(2026, 5, 20))
    b = fx.lote(db_session, "0726_0001", date(2026, 7, 3))
    fx.leitura(db_session, a, S.REJEITADA_QUALIDADE, fx.scores(0.90))
    a2, _ = fx.leitura(db_session, a, S.APROVADA_HUMANO, fx.scores(0.02))
    a3, _ = fx.leitura(db_session, a, S.REPROVADA_HUMANO, fx.scores(0.60))
    fx.leitura(db_session, a, S.FILA_HUMANA, fx.scores(0.10))
    fx.leitura(db_session, a, S.SEM_FOTO, com_foto=False)
    b1, _ = fx.leitura(db_session, b, S.APROVADA_HUMANO, fx.scores(0.03))
    fx.leitura(db_session, b, S.FILA_HUMANA, {}, modelo="nulo@v0", politica="politica@v0")
    fx.decisao(db_session, a2, aud, "aprovada")
    fx.decisao(db_session, a3, aud, "reprovada", "fora_de_foco")
    fx.decisao(db_session, b1, aud, "aprovada")
    c1, _ = fx.leitura(db_session, b, S.APROVADA_HUMANO, fx.scores(0.04))
    fx.decisao(db_session, c1, contas["auditor"].id, "aprovada")
    db_session.commit()
    return {"a": a, "b": b}


def test_qualidade_contagens_taxas_baseline_e_por_lote(client, contas, cenario):
    cab = login(client, contas["supervisor"])
    r = client.get("/metricas/qualidade", headers=cab)
    assert r.status_code == 200, r.text
    m = r.json()
    base = m["baseline"]
    assert base["lotes"] == [cenario["a"].nome, cenario["b"].nome]
    assert base["com_foto"] == 7
    assert base["contagens"] == {
        "decidida_conta_teste": 1,
        "auto_rejeitada": 1,
        "auto_aprovada": 0,
        "fila_humana": 2,
        "aprovada_humano": 2,
        "reprovada_humano": 1,
        "em_processamento": 0,
    }
    assert base["taxas"]["auto_rejeitada"] == pytest.approx(1 / 7)
    assert base["taxas"]["aprovada_humano"] == pytest.approx(2 / 7)
    lote_a, lote_b = m["por_lote"]
    assert lote_a["lote"] == cenario["a"].nome and lote_a["com_foto"] == 4
    assert all(lote_a["taxas"][k] == 0.25 for k in ("auto_rejeitada", "fila_humana"))
    assert lote_b["com_foto"] == 3 and lote_b["taxas"]["aprovada_humano"] == pytest.approx(1 / 3)
    # campos que ja existiam continuam, sem a decisao da conta de teste
    assert m["total_leituras"] == 8 and m["sem_foto"] == 1
    assert m["revisoes_por_decisao"] == {"aprovada": 2, "reprovada": 1}
    assert m["fila_humana"]["decididas"] == 3 and m["decididas_conta_teste"] == 1
    assert [p["decisoes"] for p in m["produtividade_por_auditor"]] == [3]


def test_qualidade_por_detector_e_politica(client, contas, cenario):
    cab = login(client, contas["supervisor"])
    grupos = {
        (g["detector"], g["politica"]): g
        for g in client.get("/metricas/qualidade", headers=cab).json()["por_detector"]
    }
    assert set(grupos) == {("heuristica@v1", "politica@v1"), ("nulo@v0", "politica@v0")}
    heur = grupos[("heuristica@v1", "politica@v1")]
    assert heur["com_foto"] == 6 and heur["contagens"]["auto_rejeitada"] == 1
    assert heur["contagens"]["decidida_conta_teste"] == 1
    assert grupos[("nulo@v0", "politica@v0")]["contagens"]["fila_humana"] == 1


def test_concordancia_maquina_humano_so_onde_ha_os_dois(client, contas, cenario):
    """Pares com score do heuristica@v1 e rotulo producao: a2 e b1 (passa, legivel) e
    a3 (p=0,60 passa, humano ilegivel). a1 nao tem humano; b2 nao tem score; c1 e de
    conta de teste."""
    cab = login(client, contas["supervisor"])
    conc = {
        c["fonte"]: c
        for c in client.get("/metricas/qualidade", headers=cab).json()[
            "concordancia_maquina_humano"
        ]
    }
    prod = conc["producao"]
    assert prod["n"] == 3
    assert prod["matriz"] == {
        "rejeita_ilegivel": 0,
        "rejeita_legivel": 0,
        "passa_ilegivel": 1,
        "passa_legivel": 2,
    }
    assert prod["concordancia"] == pytest.approx(2 / 3)
    assert prod["kappa"] == pytest.approx(0.0)  # maquina nunca diz ilegivel: sem acaso a bater
    assert conc["lab02"]["n"] == 0
    assert conc["lab02"]["escopo"].startswith("dentro da amostra")


def test_politica_inexistente_da_422(client, contas, cenario):
    cab = login(client, contas["supervisor"])
    for rota in ("/metricas/qualidade", "/metricas/drift", "/metricas/auto-aprovacao"):
        r = client.get(rota, params={"politica": "politica@v99"}, headers=cab)
        assert r.status_code == 422, rota


# ------------------------------------------------------------------ auto-aprovacao


def _lab02(db_session, foto_id, legivel):
    from inspecao.models import Rotulo

    db_session.add(
        Rotulo(
            foto_id=foto_id,
            rotulador_externo="ext_x",
            rodada="lab02",
            cena="digital",
            legivel=legivel,
            reflexo=False,
            fora_de_foco=not legivel,
            tampa_suja=False,
            enquadramento=False,
            display_apagado=False,
        )
    )


def test_auto_aprovacao_desligada_explica_e_nao_usa_candidato(client, db_session, contas, cenario):
    """Sem limiar_aprova o contador nao se aplica; o motivo mostra o erro que a faixa
    p_ruim <= 0,05 teria no Lab 2 (aqui 1 de 3 fotos ilegivel)."""
    for p, legivel in ((0.01, True), (0.02, False), (0.03, True), (0.5, False)):
        _, foto = fx.leitura(db_session, cenario["b"], S.FILA_HUMANA, fx.scores(p))
        _lab02(db_session, foto.id, legivel)
    db_session.commit()
    e = client.get("/metricas/auto-aprovacao", headers=login(client, contas["supervisor"])).json()
    assert e["ativa"] is False and e["contador_aplicavel"] is False
    assert e["limiar_aprova"] is None and e["fotos_na_zona"] is None
    assert (e["acumuladas_sem_erro"], e["necessarias"], e["limite_superior_erro"]) == (0, 600, None)
    d = e["diagnostico"]
    assert (d["limiar"], d["n"], d["ilegiveis"]) == (0.05, 3, 1)
    assert d["taxa_erro"] == pytest.approx(1 / 3) and 0 < d["limite_inferior_erro"] < 1 / 3
    assert "1 das 3 fotos" in e["motivo"] and "33,3%" in e["motivo"]
    assert "nao se aplica" in e["motivo"] and "M7" in e["motivo"]
    assert "candidato" not in e["motivo"]


@pytest.fixture
def politica_com_aprova(monkeypatch):
    """Politica de teste com limiar_aprova, registrada so durante o teste."""
    from inspecao.inference import politica as politica_mod
    from inspecao.inference.politica import Politica

    p = Politica(
        versao="politica@teste-aprova",
        modelo="heuristica@v1",
        limiar_rejeita=0.85,
        limiar_aprova=0.05,
    )
    monkeypatch.setitem(politica_mod.POLITICAS, p.versao, p)
    return p.versao


def test_contador_com_limiar_da_politica(client, contas, cenario, politica_com_aprova):
    e = client.get(
        "/metricas/auto-aprovacao",
        params={"politica": politica_com_aprova},
        headers=login(client, contas["supervisor"]),
    ).json()
    assert e["ativa"] is True and e["contador_aplicavel"] is True
    # zona p <= 0,05: a2 (0,02), b1 (0,03) e c1 (0,04, conta de teste: sem veredito)
    assert (e["fotos_na_zona"], e["rotuladas_na_zona"], e["erros"]) == (3, 2, 0)
    assert e["acumuladas_sem_erro"] == 2 and e["meta_atingida"] is False
    assert e["limite_superior_erro"] == pytest.approx(1 - 0.05 ** (1 / 2))
    assert "Acumuladas: 2" in e["motivo"]


def test_erro_na_zona_deixa_o_contador_em_zero(
    client, db_session, contas, cenario, politica_com_aprova
):
    aud = fx.contas_reais(db_session)["auditor"].id
    lei, _ = fx.leitura(db_session, cenario["b"], S.REPROVADA_HUMANO, fx.scores(0.01))
    fx.decisao(db_session, lei, aud, "reprovada", "display_apagado")
    # reprovada por `outro` nao tem legivel: nao conta como amostra nem como erro
    outro, _ = fx.leitura(db_session, cenario["b"], S.REPROVADA_HUMANO, fx.scores(0.01))
    fx.decisao(db_session, outro, aud, "reprovada", "outro")
    db_session.commit()
    e = client.get(
        "/metricas/auto-aprovacao",
        params={"politica": politica_com_aprova},
        headers=login(client, contas["supervisor"]),
    ).json()
    assert (e["fotos_na_zona"], e["rotuladas_na_zona"], e["erros"]) == (5, 3, 1)
    assert e["acumuladas_sem_erro"] == 0
    assert e["limite_superior_erro"] == pytest.approx(auto_aprovacao.limite_superior(1, 3))
    assert "fica em 0 para este detector e limiar" in e["motivo"]


def test_limite_inferior():
    assert auto_aprovacao.limite_inferior(0, 10) == 0.0
    assert auto_aprovacao.limite_inferior(0, 0) is None
    # 70 de 194: a taxa e ao menos ~30% com 95% (unilateral)
    assert auto_aprovacao.limite_inferior(70, 194) == pytest.approx(0.3034, abs=1e-3)


@pytest.mark.parametrize(("n", "esperado"), [(600, 0.004981), (150, 0.019773), (300, 0.009937)])
def test_limite_superior_regra_dos_tres(n, esperado):
    """Zero erro em n amostras: 1 - 0,05^(1/n), ~3/n (tabela do plano 04, secao 5.1)."""
    assert auto_aprovacao.limite_superior(0, n) == pytest.approx(esperado, abs=1e-6)
    assert auto_aprovacao.limite_superior(0, n) < 3 / n


def test_limite_superior_com_erros_e_bordas():
    # Clopper-Pearson unilateral 95% com 1 erro em 100: 0,04656 (tabela de referencia)
    assert auto_aprovacao.limite_superior(1, 100) == pytest.approx(0.04656, abs=1e-4)
    assert auto_aprovacao.limite_superior(0, 0) is None
    assert auto_aprovacao.limite_superior(5, 5) == 1.0


# ------------------------------------------------------------------ drift


@pytest.fixture
def serie_drift(db_session, contas, monkeypatch):
    """L1 e a baseline. p_ruim por foto (fora_de_foco salvo indicacao `g` = glare):

    L1: 0,9 | 0,6g | 0,0 x8                        rej 0,1 zona 0,1 conf 0,95
    L2: 0,9 x3 | 0,0 x7                            rej 0,3
    L3: 0,9 x3 | 0,6g x5 | 0,0 x2                  rej 0,3 zona 0,5 conf 0,77
    L4: igual a L3, com 2 fotos 0,0 que o auditor reprovou (discordancia 1,0)
    L5: 3 fotos: amostra insuficiente
    Baseline com 2 pares de producao concordantes (fotos 0,0 aprovadas).
    """
    monkeypatch.setattr(drift, "MIN_FOTOS", 10)
    monkeypatch.setattr(drift, "MIN_PARES_DISCORDANCIA", 2)
    aud = fx.contas_reais(db_session)["auditor"].id
    lotes = [fx.lote(db_session, f"0{i}26_0001", date(2026, i, 1)) for i in range(5, 10)]
    monkeypatch.setattr(get_settings(), "drift_lotes_baseline", [lotes[0].nome])

    def fotos(lt, ps, decidir=None):
        criadas = []
        for p in ps:
            classe = "glare" if p == 0.6 else "fora_de_foco"
            lei, _ = fx.leitura(db_session, lt, S.FILA_HUMANA, fx.scores(p, classe))
            criadas.append(lei)
        for lei in criadas[-2:] if decidir else []:
            fx.decisao(db_session, lei, aud, *decidir)
        return criadas

    fotos(lotes[0], [0.9, 0.6] + [0.0] * 8, decidir=("aprovada",))
    fotos(lotes[1], [0.9] * 3 + [0.0] * 7)
    fotos(lotes[2], [0.9] * 3 + [0.6] * 5 + [0.0] * 2)
    fotos(lotes[3], [0.9] * 3 + [0.6] * 5 + [0.0] * 2, decidir=("reprovada", "tampa_suja"))
    fotos(lotes[4], [0.9, 0.9, 0.9])
    db_session.commit()
    return lotes


def test_drift_sinais_baseline_e_alertas(client, contas, serie_drift):
    cab = login(client, contas["supervisor"])
    r = client.get("/metricas/drift", headers=cab)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["politica"] == "politica@v1" and d["detector"] == "heuristica@v1"
    base = d["baseline"]
    assert base["lotes"] == [serie_drift[0].nome] and base["n_fotos_com_score"] == 10
    v = base["valores"]
    assert v["taxa_auto_rejeicao"] == pytest.approx(0.1)
    assert v["zona_cinzenta"] == pytest.approx(0.1)
    assert v["confianca_media"] == pytest.approx(0.95)
    assert v["classes"]["fora_de_foco"] == pytest.approx(0.1)
    assert v["discordancia"] == 0.0 and base["n_pares_discordancia"] == 2

    l1, l2, l3, l4, l5 = d["lotes"]
    assert l1["na_baseline"] and not l2["na_baseline"]
    assert l1["alertas"] == []

    # L2: so a classe fora_de_foco (+20 p.p.); rejeicao fora ha 1 lote, ainda nao alerta
    assert l2["sinais"]["taxa_auto_rejeicao"]["desvio"] == pytest.approx(0.2)
    assert not l2["sinais"]["taxa_auto_rejeicao"]["alerta"]
    assert l2["sinais"]["classes"]["fora_de_foco"]["alerta"]
    assert len(l2["alertas"]) == 1

    # L3: zona cinzenta e glare sobem; confianca cai, mas so 1 lote
    s3 = l3["sinais"]
    assert s3["zona_cinzenta"]["valor"] == pytest.approx(0.5) and s3["zona_cinzenta"]["alerta"]
    assert s3["classes"]["glare"]["alerta"]
    assert s3["confianca_media"]["valor"] == pytest.approx(0.77)
    assert not s3["confianca_media"]["alerta"] and not s3["taxa_auto_rejeicao"]["alerta"]

    # L4: rejeicao fora ha 3 lotes seguidos, confianca caiu 2 seguidos, discordancia subiu
    s4 = l4["sinais"]
    assert s4["taxa_auto_rejeicao"]["alerta"] and s4["confianca_media"]["alerta"]
    assert s4["discordancia"]["valor"] == 1.0 and s4["discordancia"]["n"] == 2
    assert s4["discordancia"]["alerta"]
    assert any("3 lotes seguidos" in a for a in l4["alertas"])

    # L5: abaixo do minimo de fotos, nenhum alerta
    assert l5["amostra_insuficiente"] and l5["alertas"] == []
    assert not any(s["alerta"] for s in l5["sinais"]["classes"].values())


def test_drift_sem_baseline_nao_alerta(client, contas, serie_drift, monkeypatch):
    monkeypatch.setattr(get_settings(), "drift_lotes_baseline", ["PSP_EXTRATLEITIMPL_000000_0000"])
    d = client.get("/metricas/drift", headers=login(client, contas["supervisor"])).json()
    assert d["baseline"]["valores"] is None
    assert all(item["alertas"] == [] for item in d["lotes"])
    assert d["lotes"][1]["sinais"]["taxa_auto_rejeicao"]["valor"] == pytest.approx(0.3)


def test_drift_politica_v0_nao_tem_veredito(client, contas, serie_drift):
    d = client.get(
        "/metricas/drift",
        params={"politica": "politica@v0"},
        headers=login(client, contas["supervisor"]),
    ).json()
    assert d["detector"] is None and d["baseline"]["valores"] is None
    assert all(item["n_fotos_com_score"] == 0 for item in d["lotes"])


def test_criterio_de_alerta_publicado(client, contas, serie_drift):
    c = client.get("/metricas/drift", headers=login(client, contas["supervisor"])).json()[
        "criterio"
    ]
    assert c["desvio_classe"] == 0.10 and c["lotes_seguidos_rejeicao"] == 3
    assert math.isclose(c["queda_confianca"], 0.05) and c["z_carta_p"] == 3.0
    assert c["min_lotes_phi"] == 5


def _lotes_rejeicao(db_session, monkeypatch, rejeitadas, n_baseline):
    """Um lote de 100 fotos por contagem de rejeitadas; os `n_baseline` primeiros sao a
    baseline."""
    monkeypatch.setattr(drift, "MIN_FOTOS", 10)
    lotes = [
        fx.lote(db_session, f"1{i}26_0002", date(2026, 1, i + 1)) for i in range(len(rejeitadas))
    ]
    nomes = [lt.nome for lt in lotes[:n_baseline]]
    monkeypatch.setattr(get_settings(), "drift_lotes_baseline", nomes)
    for lt, k in zip(lotes, rejeitadas, strict=True):
        for i in range(100):
            fx.leitura(db_session, lt, S.FILA_HUMANA, fx.scores(0.9 if i < k else 0.0))
    db_session.commit()


def test_drift_carta_p_sem_phi_abaixo_de_5_lotes(client, db_session, contas, monkeypatch):
    """Baseline de 2 lotes (0 e 4 de 100): phi estimado 4/0,98, mas aplicado 1. Com a
    variancia da baseline (1/100 + 1/200), 8/100 da z = 3,50: alerta pela carta p com
    desvio de so 6 p.p.; 3/100 da z = 0,58: nao alerta."""
    _lotes_rejeicao(db_session, monkeypatch, (0, 4, 8, 3), n_baseline=2)
    d = client.get("/metricas/drift", headers=login(client, contas["supervisor"])).json()
    assert d["baseline"]["sobredispersao_estimada"]["taxa_auto_rejeicao"] == pytest.approx(4 / 0.98)
    assert d["baseline"]["sobredispersao"]["taxa_auto_rejeicao"] == 1.0
    l3 = d["lotes"][2]["sinais"]["taxa_auto_rejeicao"]
    l4 = d["lotes"][3]["sinais"]["taxa_auto_rejeicao"]
    esperado = 0.06 / math.sqrt(0.02 * 0.98 * (1 / 100 + 1 / 200))
    assert l3["z"] == pytest.approx(esperado) and esperado > 3
    assert l3["alerta"] and l3["desvio"] == pytest.approx(0.06)
    assert any(a.startswith("carta p, taxa_auto_rejeicao") for a in d["lotes"][2]["alertas"])
    assert l4["z"] == pytest.approx(0.01 / math.sqrt(0.02 * 0.98 * (1 / 100 + 1 / 200)))
    assert not l4["alerta"]


def test_drift_carta_p_com_phi_a_partir_de_5_lotes(client, db_session, contas, monkeypatch):
    """Baseline de 5 lotes (0, 4, 0, 4, 2 de 100): p = 0,02, phi = (4 * 4/1,96) / 4 = 2,04,
    aplicado. O mesmo 8/100 agora da z = 2,74: nao alerta."""
    _lotes_rejeicao(db_session, monkeypatch, (0, 4, 0, 4, 2, 8), n_baseline=5)
    d = client.get("/metricas/drift", headers=login(client, contas["supervisor"])).json()
    phi = d["baseline"]["sobredispersao"]["taxa_auto_rejeicao"]
    assert phi == pytest.approx(4 * (4 / 1.96) / 4)
    s = d["lotes"][5]["sinais"]["taxa_auto_rejeicao"]
    assert s["z"] == pytest.approx(0.06 / math.sqrt(phi * 0.02 * 0.98 * (1 / 100 + 1 / 500)))
    assert s["z"] < 3 and not s["alerta"]


def test_lab02_de_detector_treinado_depende_do_split_dele(
    client, db_session, contas, cenario, monkeypatch
):
    """Detector treinado sem lotes de teste declarados: sem concordancia contra o Lab 2
    (nada de recorte por DRIFT_LOTES_BASELINE). Com os lotes declarados, so eles entram."""
    from inspecao.inference import politica as politica_mod
    from inspecao.inference.politica import Politica
    from inspecao.monitoramento import qualidade as qualidade_mod

    p = Politica(versao="politica@teste-treinada", modelo="heuristica@treinada", limiar_rejeita=0.5)
    monkeypatch.setitem(politica_mod.POLITICAS, p.versao, p)
    for lt in (cenario["a"], cenario["b"]):
        _, foto = fx.leitura(db_session, lt, S.FILA_HUMANA, fx.scores(0.9), modelo=p.modelo)
        _lab02(db_session, foto.id, False)
    db_session.commit()
    cab = login(client, contas["supervisor"])

    def lab02():
        q = client.get("/metricas/qualidade", params={"politica": p.versao}, headers=cab).json()
        return next(c for c in q["concordancia_maquina_humano"] if c["fonte"] == "lab02")

    sem_split = lab02()
    assert sem_split["n"] == 0 and "pendente da integracao com o M7" in sem_split["escopo"]
    monkeypatch.setitem(qualidade_mod.LOTES_DE_TESTE, p.modelo, (cenario["b"].nome,))
    com_split = lab02()
    assert com_split["n"] == 1 and com_split["escopo"] == "so os lotes de teste do detector"

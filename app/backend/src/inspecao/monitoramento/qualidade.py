"""Contagens e taxas por decisao, por lote e por detector/politica (T-M8-02).

Buckets de decisao, sobre o status atual de cada leitura com foto:

| bucket            | status                                      |
| auto_rejeitada    | rejeitada_qualidade, auto_rejeitada         |
| auto_aprovada     | auto_aprovada                               |
| fila_humana       | fila_humana (pendente)                      |
| aprovada_humano   | aprovada_humano                             |
| reprovada_humano  | reprovada_humano                            |
| em_processamento  | recebida, triagem_qualidade, validacao_leitura |
| decidida_conta_teste | aprovada/reprovada_humano por conta de teste (E2E, demo) |

Decisao de conta de teste (`inspecao.contas_teste`) nao e decisao de auditor: fica num
bucket proprio, fora de aprovada/reprovada (no banco local, as 55 revisoes de E2E e demo).

Taxa = bucket / leituras com foto (status diferente de `sem_foto`). A baseline e o
agregado dos lotes do banco: no banco real, exatamente os 4 lotes ingeridos, a
medida que o AS-IS nao tinha (plano 02).

Detector/politica de uma leitura: a ultima saida de triagem gravada no historico por
um ator `modelo:<detector>` (rejeitada_qualidade, validacao_leitura ou fila_humana),
com a versao da politica em `detalhe.politica`. Leitura sem essa linha (ainda nao
triada, ou triada antes do M5 gravar a politica) fica como `sem_registro`.
"""

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from inspecao.contas_teste import decidida_por_teste
from inspecao.enums import StatusLeitura
from inspecao.inference.politica import Politica
from inspecao.models import HistoricoStatus, Leitura, Lote
from inspecao.monitoramento import sinais

S = StatusLeitura
BUCKETS: dict[str, tuple[StatusLeitura, ...]] = {
    "auto_rejeitada": (S.REJEITADA_QUALIDADE, S.AUTO_REJEITADA),
    "auto_aprovada": (S.AUTO_APROVADA,),
    "fila_humana": (S.FILA_HUMANA,),
    "aprovada_humano": (S.APROVADA_HUMANO,),
    "reprovada_humano": (S.REPROVADA_HUMANO,),
    "em_processamento": (S.RECEBIDA, S.TRIAGEM_QUALIDADE, S.VALIDACAO_LEITURA),
}
BUCKET_DO_STATUS = {st.value: b for b, sts in BUCKETS.items() for st in sts}
TESTE = "decidida_conta_teste"
BUCKET_DO_STATUS[TESTE] = TESTE
# Detectores sem treino nem calibracao sobre o Lab 2 alem do limiar do M5. Para os
# treinados (M7), a concordancia contra o Lab 2 so vale nos lotes de TESTE do proprio
# detector, declarados aqui junto com ele (o contrato do modelo, `qualidade.json` do M7,
# e quem os define). Detector treinado sem lotes de teste declarados: sem concordancia
# contra o Lab 2, em vez de um recorte adivinhado.
DETECTORES_SEM_TREINO = {"heuristica@v1", "nulo@v0"}
LOTES_DE_TESTE: dict[str, tuple[str, ...]] = {}


def _status():
    """Status da leitura, com a decisao de conta de teste separada."""
    humano = Leitura.status_atual.in_([S.APROVADA_HUMANO.value, S.REPROVADA_HUMANO.value])
    return case((humano & decidida_por_teste(Leitura.id), TESTE), else_=Leitura.status_atual).label(
        "status"
    )


SAIDAS_DA_TRIAGEM = (S.REJEITADA_QUALIDADE, S.VALIDACAO_LEITURA, S.FILA_HUMANA)
SEM_REGISTRO = "sem_registro"


def decisoes(por_status: dict[str, int]) -> dict[str, Any]:
    """{com_foto, contagens, taxas} a partir da contagem por status."""
    contagens = dict.fromkeys([*BUCKETS, TESTE], 0)
    for st, n in por_status.items():
        if st in BUCKET_DO_STATUS:
            contagens[BUCKET_DO_STATUS[st]] += n
    com_foto = sum(contagens.values())
    taxas = {b: (n / com_foto if com_foto else None) for b, n in contagens.items()}
    return {"com_foto": com_foto, "contagens": contagens, "taxas": taxas}


def _somar(destino: dict[str, int], origem: dict[str, int]) -> None:
    for k, n in origem.items():
        destino[k] = destino.get(k, 0) + n


def por_lote(db: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(baseline com os lotes do banco, lista por lote em ordem de extracao)."""
    st = _status()
    contagem = db.execute(
        select(Leitura.lote_id, st, func.count()).group_by(Leitura.lote_id, st)
    ).all()
    por_id: dict[int, dict[str, int]] = {}
    for lote_id, st, n in contagem:
        por_id.setdefault(lote_id, {})[st] = n
    lotes = db.scalars(select(Lote).order_by(Lote.data_extracao, Lote.nome)).all()
    total: dict[str, int] = {}
    itens = []
    for lote in lotes:
        st = por_id.get(lote.id, {})
        _somar(total, st)
        itens.append({"lote": lote.nome, "data_extracao": lote.data_extracao} | decisoes(st))
    baseline = {"lotes": [lote.nome for lote in lotes]} | decisoes(total)
    return baseline, itens


def por_detector(db: Session) -> list[dict[str, Any]]:
    ultima = (
        select(
            HistoricoStatus.leitura_id,
            HistoricoStatus.ator,
            HistoricoStatus.detalhe["politica"].astext.label("politica"),
        )
        .where(
            HistoricoStatus.ator.like("modelo:%"),
            HistoricoStatus.status.in_([s.value for s in SAIDAS_DA_TRIAGEM]),
        )
        .distinct(HistoricoStatus.leitura_id)
        .order_by(HistoricoStatus.leitura_id, HistoricoStatus.id.desc())
        .subquery()
    )
    st = _status()
    linhas = db.execute(
        select(ultima.c.ator, ultima.c.politica, st, func.count())
        .select_from(Leitura)
        .outerjoin(ultima, ultima.c.leitura_id == Leitura.id)
        .where(Leitura.status_atual != S.SEM_FOTO.value)
        .group_by(ultima.c.ator, ultima.c.politica, st)
    ).all()
    grupos: dict[tuple[str, str], dict[str, int]] = {}
    for ator, politica, st, n in linhas:
        detector = ator.removeprefix("modelo:") if ator else SEM_REGISTRO
        chave = (detector, politica or SEM_REGISTRO)
        _somar(grupos.setdefault(chave, {}), {st: n})
    return [{"detector": d, "politica": p} | decisoes(st) for (d, p), st in sorted(grupos.items())]


def _concordancia(
    politica: Politica,
    fonte: str,
    escopo: str,
    humano: dict[int, bool],
    resultados: list[sinais.Resultado],
) -> dict[str, Any]:
    c = sinais.Concordancia()
    for r in resultados:
        if r.foto_id not in humano:
            continue
        ilegivel = sinais.maquina_ilegivel(politica, sinais.p_ruim(politica, r.saida))
        if ilegivel is not None:
            c.adicionar(ilegivel, humano[r.foto_id])
    return {
        "fonte": fonte,
        "escopo": escopo,
        "politica": politica.versao,
        "n": c.n,
        "matriz": c.matriz,
        "concordancia": c.concordancia,
        "kappa": c.kappa,
    }


def concordancia_maquina_humano(db: Session, politica: Politica) -> list[dict[str, Any]]:
    """Maquina ("ilegivel" = p_ruim >= limiar_rejeita) contra humano, onde ha os dois.

    Duas fontes, separadas para nao misturar populacoes:
    - `producao`: decisao de auditor (contas reais). So ve o que a maquina mandou para a
      fila, entao nunca tem par "rejeita";
    - `lab02`: o Lab 2 e o conjunto de calibracao do limiar do M5, logo DENTRO da
      amostra: o "zero falso positivo na rejeicao" vale por construcao, e e pelo mesmo
      motivo que o Lab 2 fica fora do contador da auto-aprovacao. Para detector treinado
      (M7), so as fotos dos lotes de teste do detector (`LOTES_DE_TESTE`) entram.
    """
    resultados = sinais.ultimos_resultados(db, politica.modelo)
    escopo_lab02 = "dentro da amostra (calibracao do M5)"
    resultados_lab02 = resultados
    if politica.modelo not in DETECTORES_SEM_TREINO:
        teste = LOTES_DE_TESTE.get(politica.modelo or "", ())
        ids = set(db.scalars(select(Lote.id).where(Lote.nome.in_(teste)))) if teste else set()
        resultados_lab02 = [r for r in resultados if r.lote_id in ids]
        escopo_lab02 = (
            "so os lotes de teste do detector"
            if teste
            else "split do detector nao declarado: pendente da integracao com o M7"
        )
    return [
        _concordancia(
            politica,
            "producao",
            "decisoes de auditor (fora da amostra)",
            sinais.veredito_producao(db),
            resultados,
        ),
        _concordancia(politica, "lab02", escopo_lab02, sinais.veredito_lab02(db), resultados_lab02),
    ]

"""Montagem direta de lotes, leituras, scores e decisoes para os testes do M8.

Sem ingestao nem imagem: as metricas do M8 so leem o banco. Tudo ficticio (lotes
com data impossivel no nome, posicao e medidor inventados). Nao e arquivo de teste.
"""

from datetime import date

from sqlalchemy.orm import Session

from inspecao.enums import StatusLeitura
from inspecao.models import Foto, HistoricoStatus, Leitura, Lote, ResultadoInferencia, RevisaoHumana
from inspecao.rotulagem import producao

SAIDA_DA_TRIAGEM = {
    StatusLeitura.REJEITADA_QUALIDADE: StatusLeitura.REJEITADA_QUALIDADE,
    StatusLeitura.FILA_HUMANA: StatusLeitura.FILA_HUMANA,
    StatusLeitura.APROVADA_HUMANO: StatusLeitura.FILA_HUMANA,
    StatusLeitura.REPROVADA_HUMANO: StatusLeitura.FILA_HUMANA,
}
_seq = {"n": 0}


def lote(db: Session, sufixo: str, quando: date) -> Lote:
    """Lote `PSP_EXTRATLEITIMPL_99<sufixo>`: o 99 garante que nunca e um lote real."""
    lt = Lote(
        nome=f"PSP_EXTRATLEITIMPL_99{sufixo}",
        data_extracao=quando,
        csv_nome=f"{sufixo}.csv",
        checksum_csv="0" * 64,
    )
    db.add(lt)
    db.flush()
    return lt


def scores(p: float, classe: str = "fora_de_foco") -> dict:
    """Saida do detector com uma unica classe acesa em `p` (p_ruim = p na v1)."""
    base = dict.fromkeys(
        ("fora_de_foco", "glare", "tampa_suja", "enquadramento_incorreto", "display_apagado"), 0.0
    )
    return base | {classe: p, "sinais": {"tenengrad": 1.0}}


def leitura(
    db: Session,
    lt: Lote,
    status: StatusLeitura,
    saida: dict | None = None,
    modelo: str = "heuristica@v1",
    politica: str = "politica@v1",
    com_foto: bool = True,
) -> tuple[Leitura, Foto | None]:
    """Leitura no `status`, com foto principal, resultado do detector e a linha de
    historico da saida da triagem (ator `modelo:<detector>`, politica no detalhe)."""
    _seq["n"] += 1
    n = _seq["n"]
    lei = Leitura(lote_id=lt.id, linha_csv=n + 1, posicao_lida=1000 + n, status_atual=status.value)
    db.add(lei)
    db.flush()
    if not com_foto:
        return lei, None
    foto = Foto(
        lote_id=lt.id,
        leitura_id=lei.id,
        principal=True,
        nome_arquivo=f"{lt.nome}_{n:020d}_000.jpg",
        id_externo=f"{n:020d}",
        sequencia=0,
        caminho_storage=f"{lt.nome}/{n}.jpg",
        largura=360,
        altura=480,
        bytes=1,
        sha256=f"{n:064d}",
    )
    db.add(foto)
    db.flush()
    if saida is not None:
        db.add(ResultadoInferencia(foto_id=foto.id, modelo=modelo, saida=saida))
    if status in SAIDA_DA_TRIAGEM:
        db.add(
            HistoricoStatus(
                leitura_id=lei.id,
                status=SAIDA_DA_TRIAGEM[status].value,
                ator=f"modelo:{modelo}",
                detalhe={"politica": politica},
            )
        )
    db.flush()
    return lei, foto


def decisao(
    db: Session, lei: Leitura, usuario_id: int, decisao: str, motivo: str | None = None
) -> RevisaoHumana:
    """Revisao humana com o rotulo `producao` correspondente (o que o POST grava)."""
    from inspecao.enums import DecisaoHumana, MotivoReprovacao

    rev = RevisaoHumana(leitura_id=lei.id, usuario_id=usuario_id, decisao=decisao, motivo=motivo)
    db.add(rev)
    db.flush()
    producao.registrar(
        db,
        lei.id,
        usuario_id,
        DecisaoHumana(decisao),
        MotivoReprovacao(motivo) if motivo else None,
        revisao_id=rev.id,
    )
    return rev


# Dominio ficticio fora de `contas_teste.DOMINIOS_DE_TESTE`: o M8 so mede decisao de conta
# real, e as contas de `usuarios_fixture` (@teste.com.br) contam como E2E.
DOMINIO_REAL = "@campo.example"


def contas_reais(db: Session) -> dict:
    """Um usuario real de cada papel (e um segundo auditor). Idempotente na transacao."""
    from sqlalchemy import select

    from inspecao.enums import Papel
    from inspecao.models import Usuario
    from tests.usuarios_fixture import Conta, criar_usuario

    def conta(nome: str, papel: Papel) -> Conta:
        email = f"{nome}{DOMINIO_REAL}"
        existente = db.scalar(select(Usuario).where(Usuario.email == email))
        if existente is not None:
            return Conta(id=existente.id, email=email, papel=papel)
        return criar_usuario(db, email, papel)

    return {
        "auditor": conta("auditora", Papel.AUDITOR),
        "auditor2": conta("auditor2", Papel.AUDITOR),
        "auditor3": conta("auditor3", Papel.AUDITOR),
        "supervisor": conta("supervisora", Papel.SUPERVISOR),
        "admin": conta("admin", Papel.ADMIN),
    }

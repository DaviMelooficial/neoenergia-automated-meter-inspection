"""`ml/reports/kappa.md` (T-M6-07): so agregados, nenhum rotulo por foto.

Sem data no corpo: o arquivo e versionado e muda so quando os numeros mudam.
"""

from collections.abc import Mapping, Sequence

from inspecao.enums import RodadaRotulagem
from inspecao.rotulagem.consolidacao import CLASSES, LIMIAR_KAPPA, KappaClasse, KappaPar


def _fmt(k: float | None) -> str:
    return "n/d" if k is None else f"{k:.3f}".replace(".", ",")


def _gate(k: KappaClasse) -> str:
    if k.kappa is None:
        return "sem dado"
    return "passa" if k.passa else f"**não passa** (< {LIMIAR_KAPPA:.1f})".replace(".", ",")


DESVIOS = """\
## Desvios em relação ao plano

1. **Amostra das rodadas A e B = as 329 fotos do Lab 2** (rodada `lab02`). A amostra
   estratificada de 300 fotos do T-M6-02 (lote, nota, faixa do score heurístico) não foi
   construída. As rodadas A e B servem hoje para remedir o esquema v2 sobre o que o Lab 2
   já rotulou, como pede `lab-02/rotulos/esquema.md`.
2. **Desempate cego.** O terceiro rotulador recebe a foto divergente sem ver as duas
   versões, o que contraria a regra 4 do doc 05, seção 4 ("a rodada de desempate é a única
   que mostra as duas versões"). Escolhido para manter a cegueira estrita no servidor; a
   maioria de 3 votos resolve o empate. **Decisão pendente do Davi.**
3. **Sem reserva de foto na rotulagem.** Dois supervisores na mesma rodada podem receber
   a mesma foto ao mesmo tempo; a foto pode ganhar 3 votos ou mais e a dupla deixa de ser
   dupla. A maioria e o kappa por par toleram isso, mas o n de cada par passa a não ser o
   desenho do plano.
4. **Export em CSV, não parquet** (T-M6-08): `ml/artifacts/rotulos.csv` e
   `GET /rotulagem/export` (só admin, porque traz o consolidado por foto). O backend não
   tem pyarrow; o `ml/` converte se precisar. A coluna `lote` é o nome completo do lote
   no banco, não o "220526" do CSV do Lab 2.
5. **Consolidação com as regras v1 de `rotulador/consolidar.py`**, para que o kappa do
   `lab02` bata com `lab-02/resultados/resumo.json`. A regra v2 "display apagado implica
   não legível" vale para os rótulos novos (o servidor recusa com 422); o `lab02` tem 1
   rótulo que a fere.
6. **`POST /rotulagem/proxima`**, e não o `GET` do doc 05, seção 4: segue o doc 03
   (seção 4.2) e a fila humana, e a rota não é cacheável por proxy.
7. **Kappa das rodadas cegas só para admin ou para quem não votou nelas.** Com n pequeno,
   o kappa por par antes e depois do próprio voto revela o voto do outro (diferencial).
   O `lab02` já fechou e fica aberto a supervisor.

## Risco residual

A cegueira é garantida nos endpoints de rotulagem, não no papel: o supervisor também é
auditor e acessa `/leituras` e `/fila-humana`, onde vê posição lida, nota do leiturista,
score e decisões. Um rotulador que abrir a mesma foto por lá deixa de ser cego. Mitigação
possível: rotuladores sem papel de auditor no período das rodadas, ou separar o papel de
rotulador.
"""


def gerar(
    rodadas: Sequence[RodadaRotulagem],
    medio: Mapping[str, KappaClasse],
    pares: Sequence[KappaPar],
    contagens: Mapping[str, int],
    rotulos: int,
    fotos: int,
    fotos_dupla: int,
    fotos_empate: int,
    controle_com_dupla: int | None,
) -> str:
    md = [
        "# Kappa de Cohen por classe (M6)",
        "",
        "Gerado por `make rotulos-kappa` (`python -m inspecao.rotulagem.cli kappa`). Só"
        " agregados: nenhum rótulo por foto.",
        "",
        f"Rodadas consolidadas: {', '.join(r.value for r in rodadas)}. Rótulos: {rotulos};"
        f" fotos: {fotos}; com dupla rotulagem: {fotos_dupla}; com empate na maioria:"
        f" {fotos_empate}.",
    ]
    if controle_com_dupla is not None:
        md.append(f"Fotos de controle com 2 ou mais rótulos: {controle_com_dupla}.")
    md += [
        "",
        f"## Kappa médio ponderado por n (gate: κ ≥ {LIMIAR_KAPPA:.1f})".replace(".", ","),
        "",
        "| classe | κ | n (pares-foto) | gate |",
        "|---|--:|--:|---|",
    ]
    md += [f"| {c} | {_fmt(medio[c].kappa)} | {medio[c].n} | {_gate(medio[c])} |" for c in CLASSES]
    md += ["", "## Por par de rotuladores", ""]
    if pares:
        md += ["| par | fotos | " + " | ".join(CLASSES) + " |", "|---|--:|" + "--:|" * len(CLASSES)]
        md += [
            f"| {p.a} × {p.b} | {p.n} | " + " | ".join(_fmt(p.kappa[c]) for c in CLASSES) + " |"
            for p in pares
        ]
    else:
        md.append("Nenhum par de rotuladores com foto em comum.")
    md.append("")
    md.append("n/d = kappa indefinido (os dois usaram uma única categoria em todas as fotos).")

    abaixo = [c for c in CLASSES if not medio[c].passa]
    md += [
        "",
        "## Aceite do M6: o que ainda não está cumprido",
        "",
        "| rodada | rótulos no banco |",
        "|---|--:|",
    ]
    md += [f"| {r.value} | {contagens.get(r.value, 0)} |" for r in RodadaRotulagem]
    md += [
        "",
        "- As rodadas A e B com o esquema v2 ainda não rodaram."
        if contagens.get("a", 0) == 0 or contagens.get("b", 0) == 0
        else "- As rodadas A e B têm rótulos; o aceite pede 300 fotos com rótulo consolidado.",
        "- A amostra estratificada de 300 fotos (T-M6-02) não existe.",
        f"- {len(abaixo)} de {len(CLASSES)} classes ficam abaixo de {LIMIAR_KAPPA:.1f}".replace(
            ".", ","
        )
        + (f" nestas rodadas: {', '.join(abaixo)}." if abaixo else "."),
        "  Pelo gate do M6, essas classes não seguem para o treino até uma nova rodada passar.",
        "",
        DESVIOS,
    ]
    return "\n".join(md)

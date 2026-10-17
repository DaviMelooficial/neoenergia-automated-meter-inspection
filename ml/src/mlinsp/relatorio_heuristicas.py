"""Relatorio do marco M5: distribuicao dos sinais, calibracao do limiar e amostra de inspecao.

Duas fontes, cada uma no que e a fonte certa:
- as 12.340 fotos de "Base cliente/", lidas aqui com `features_v0` (os mesmos numeros
  que o backend grava: paridade em app/backend/tests/test_heuristics.py), para os
  histogramas dos sinais brutos;
- a exportacao do banco (`make relatorio-heuristicas` gera o CSV), com os scores e a
  decisao que o worker de fato gravou para cada leitura, para a calibracao.

Evidencia de falso positivo: os rotulos humanos do Lab 2 (`lab-02/rotulos/rotulos.csv`)
e as decisoes humanas da fila. Uma foto auto-rejeitada que QUALQUER rotulador marcou
legivel, ou que um auditor aprovou, conta como falso positivo.

    uv run python -m mlinsp.relatorio_heuristicas --exportacao <csv> --base "../Base cliente"
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from urllib.parse import quote

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from mlinsp.dados import ler_manifesto
from mlinsp.heuristicas import NOMES_V0, features_v0_lote

CLASSES = ["fora_de_foco", "glare", "tampa_suja", "enquadramento_incorreto", "display_apagado"]
# Os 5 sinais do plano 04 (secao 2) e o sinal que o detector usa para foco.
SINAIS_PLANO = {
    "lap_var": "nitidez (var. Laplaciano)",
    "tenengrad": "tenengrad (score de foco)",
    "frac_estourados": "glare_ratio",
    "frac_escuros": "escuridao",
    "contraste": "contraste",
    "bordas_centro": "entropia_bordas (Canny centro)",
}
ESCALA_LOG = {"lap_var", "tenengrad"}
# Contas de teste E2E, demo e seed (dominios de exemplo): decisao delas nao e auditoria
# e nao entra como evidencia. No banco local de 2026-10-16 eram as 55 da fila
# (auditor.e2e@exemplo.com.br e demo@exemplo.com.br).
DOMINIOS_DE_TESTE = ("@exemplo.com.br", "@teste.com.br")
GRADE = np.round(np.arange(0.50, 1.0001, 0.025), 3)
N_PIORES = 100
N_LIMITROFES = 100


def ler_exportacao(caminho: Path) -> pd.DataFrame:
    """Uma linha por leitura com foto: arquivo, lote, status, saida (json), latencia_ms,
    decisao_humana, email_revisor. `p_ruim` = maior score entre as 5 classes, como na
    politica; `sinais` guarda os sinais brutos gravados pelo backend."""
    df = pd.read_csv(caminho)
    saidas = df.pop("saida").map(json.loads)
    for c in CLASSES:
        df[c] = saidas.map(lambda s, c=c: s.get(c))
    df["sinais"] = saidas.map(lambda s: s.get("sinais", {}))
    df["p_ruim"] = df[CLASSES].max(axis=1)
    df["classe_max"] = df[CLASSES].idxmax(axis=1)
    return df.set_index("arquivo")


def ler_rotulos(caminho: Path) -> pd.DataFrame:
    r = pd.read_csv(caminho)
    return r.groupby("nome_arquivo").agg(
        legivel_algum=("legivel", "max"),
        legivel_todos=("legivel", "min"),
        n_rotuladores=("legivel", "size"),
        cenas=("cena", lambda s: "/".join(sorted(set(s)))),
    )


def evidencia(exp: pd.DataFrame, rot: pd.DataFrame) -> pd.DataFrame:
    """Leituras com alguma verdade humana: `legivel` = 1 se algum humano leu.

    Decisoes da fila feitas por contas de DOMINIOS_DE_TESTE ficam de fora."""
    lab = exp.join(rot, how="inner")
    lab = lab.assign(legivel=lab.legivel_algum, fonte="rotulo_lab02")
    de_teste = exp.email_revisor.fillna("").str.lower().str.endswith(DOMINIOS_DE_TESTE)
    hum = exp[exp.decisao_humana.notna() & ~de_teste & ~exp.index.isin(lab.index)]
    hum = hum.assign(legivel=(hum.decisao_humana == "aprovada").astype(int), fonte="fila_humana")
    return pd.concat([lab, hum])


def paridade(feat: pd.DataFrame, exp: pd.DataFrame) -> dict:
    """Compara `features_v0` (calculado aqui) com os sinais que o backend gravou, foto a
    foto, sem passar por CSV: a medida de paridade do report."""
    gravado = pd.DataFrame(list(exp.sinais), index=exp.index)[NOMES_V0]
    calculado = feat.loc[gravado.index, NOMES_V0]
    dif = (calculado - gravado).abs()
    rel = dif / gravado.abs().where(gravado.abs() > 0, 1.0)
    return {
        "fotos": len(gravado),
        "max_dif_abs": float(dif.max().max()),
        "max_dif_rel": float(rel.max().max()),
        "fotos_com_diferenca": int((dif > 0).any(axis=1).sum()),
    }


def tabela_limiar(exp: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for t in GRADE:
        rej = exp.p_ruim >= t
        rej_ev = ev[ev.p_ruim >= t]
        linhas.append(
            {
                "limiar": t,
                "auto_rejeitadas": int(rej.sum()),
                "cobertura": float(rej.mean()),
                "com_evidencia": len(rej_ev),
                "falsos_positivos": int(rej_ev.legivel.sum()),
            }
        )
    return pd.DataFrame(linhas)


def _milhar(n: int) -> str:
    return f"{n:_}".replace("_", ".")


def _hist(ax, x: pd.Series, nome: str, log: bool) -> None:
    x = x.astype(float)
    if log:
        x = np.log10(x + 1.0)
        nome = f"log10(1 + {nome})"
    ax.hist(x, bins=80, color="#4C72B0", alpha=0.85)
    for q, estilo in ((0.01, ":"), (0.05, "--"), (0.5, "-")):
        ax.axvline(x.quantile(q), color="#C44E52", ls=estilo, lw=1, label=f"p{int(q * 100)}")
    ax.set_title(nome, fontsize=9)
    ax.tick_params(labelsize=7)


def figura_sinais(feat: pd.DataFrame, saida: Path) -> None:
    fig, eixos = plt.subplots(2, 3, figsize=(12, 6.5))
    for ax, (c, rotulo) in zip(eixos.ravel(), SINAIS_PLANO.items()):
        _hist(ax, feat[c], f"{rotulo}", c in ESCALA_LOG)
    eixos[0, 0].legend(fontsize=7)
    fig.suptitle(f"Sinais brutos nas {_milhar(len(feat))} fotos da base", fontsize=11)
    fig.tight_layout()
    fig.savefig(saida, dpi=110)
    plt.close(fig)


def figura_scores(exp: pd.DataFrame, limiar: float, saida: Path) -> None:
    fig, eixos = plt.subplots(2, 3, figsize=(12, 6.5))
    colunas = CLASSES + ["p_ruim"]
    for ax, c in zip(eixos.ravel(), colunas):
        ax.hist(exp[c], bins=np.linspace(0, 1, 41), color="#55A868", alpha=0.85, log=True)
        ax.axvline(limiar, color="#C44E52", lw=1.2, label=f"limiar {limiar:.2f}")
        ax.set_title(c, fontsize=9)
        ax.tick_params(labelsize=7)
    eixos[1, 2].legend(fontsize=7)
    fig.suptitle(
        f"Scores de heuristica@v1 gravados em resultados_inferencia ({_milhar(len(exp))} "
        "leituras, eixo y em log)",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(saida, dpi=110)
    plt.close(fig)


def figura_limiar(tab: pd.DataFrame, limiar: float, saida: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 4))
    ax.plot(tab.limiar, tab.cobertura * 100, "o-", ms=3, color="#4C72B0", label="% auto-rejeitado")
    ax.axhspan(5, 15, color="#8172B2", alpha=0.12, label="meta do plano (5% a 15%)")
    ax.axvline(limiar, color="#C44E52", lw=1.2, label=f"limiar escolhido {limiar:.2f}")
    fp = tab[tab.falsos_positivos > 0]
    if not fp.empty:
        ax.axvspan(tab.limiar.min(), fp.limiar.max(), color="#C44E52", alpha=0.08,
                   label="com falso positivo nas rotuladas")
    ax.set_xlabel("limiar_rejeita (p_ruim >= limiar)")
    ax.set_ylabel("% das leituras com foto")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(saida, dpi=110)
    plt.close(fig)


def amostra_inspecao(exp: pd.DataFrame, limiar: float) -> pd.DataFrame:
    """100 piores (maior p_ruim) e 100 limitrofes (as 100 mais proximas do limiar,
    metade logo acima e metade logo abaixo; se faltar de um lado, completa com o outro)."""
    ordem = exp.sort_values("p_ruim", ascending=False)
    piores = ordem.head(N_PIORES).assign(grupo="pior")
    resto = ordem.drop(piores.index)
    acima = resto[resto.p_ruim >= limiar].sort_values("p_ruim")
    abaixo = resto[resto.p_ruim < limiar].sort_values("p_ruim", ascending=False)
    n_acima = min(len(acima), N_LIMITROFES // 2)
    n_abaixo = N_LIMITROFES - n_acima
    limitrofes = pd.concat([acima.head(n_acima), abaixo.head(n_abaixo)]).assign(grupo="limitrofe")
    am = pd.concat([piores, limitrofes])
    am["decisao_v1"] = np.where(am.p_ruim >= limiar, "rejeitar", "humano")
    am["legivel_inspecao"] = ""  # preenchido pela inspecao humana: sim / nao / duvida
    am["inspetor"] = ""
    return am[["lote", "grupo", "p_ruim", "classe_max", "decisao_v1", *CLASSES,
               "legivel_inspecao", "inspetor"]]


def pagina_inspecao(am: pd.DataFrame, base_rel: str, saida: Path) -> None:
    """HTML local que mostra as 200 fotos a partir de "Base cliente/" (nada e copiado)
    e exporta um CSV com a marcacao de cada uma. Abrir direto do disco, no repo."""
    cartoes = []
    for i, (arquivo, r) in enumerate(am.iterrows()):
        src = html.escape(quote(f"{base_rel}/{r.lote}/{arquivo}"))
        cartoes.append(
            f'<figure data-arquivo="{html.escape(arquivo)}" data-grupo="{r.grupo}">'
            f'<img loading="lazy" src="{src}" alt="">'
            f"<figcaption><b>{i + 1}</b> · {r.grupo} · p_ruim {r.p_ruim:.3f} · "
            f"{r.classe_max} · v1: {r.decisao_v1}<br>"
            + "".join(
                f'<label><input type="radio" name="f{i}" value="{v}"> {v}</label>'
                for v in ("sim", "nao", "duvida")
            )
            + "</figcaption></figure>"
        )
    pagina = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inspecao M5</title>
<style>
:root {{ --fundo:#f6f6f4; --texto:#1d1d1b; --cartao:#fff; --borda:#ddd; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fundo:#161616; --texto:#eee;
  --cartao:#222; --borda:#444; }} }}
body {{ background:var(--fundo); color:var(--texto); font:14px system-ui, sans-serif;
  margin:0; padding:16px; }}
header {{ position:sticky; top:0; background:var(--fundo); padding:8px 0; z-index:1; }}
main {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:12px; }}
figure {{ margin:0; background:var(--cartao); border:1px solid var(--borda); padding:6px; }}
img {{ width:100%; height:auto; display:block; cursor:zoom-in; }}
img.zoom {{ position:fixed; inset:0; margin:auto; width:auto; max-width:100vw;
  max-height:100vh; z-index:2; cursor:zoom-out; }}
figcaption {{ font-size:12px; margin-top:4px; }} label {{ margin-right:10px; }}
</style></head><body>
<header><b>Inspecao das 200 fotos dos extremos (M5)</b> · legivel = da para ler com
seguranca TODOS os digitos? · nome do inspetor <input id="inspetor" size="14">
<button id="exportar">exportar CSV</button> <span id="contagem"></span></header>
<main>{"".join(cartoes)}</main>
<script>
const figs = [...document.querySelectorAll("figure")];
const contar = () => {{ document.getElementById("contagem").textContent =
  figs.filter(f => f.querySelector("input:checked")).length + " de " + figs.length; }};
document.addEventListener("change", contar); contar();
document.querySelectorAll("img").forEach(i => i.onclick = () => i.classList.toggle("zoom"));
document.getElementById("exportar").onclick = () => {{
  const quem = document.getElementById("inspetor").value.trim();
  const linhas = ["arquivo,grupo,legivel_inspecao,inspetor"].concat(figs.map(f => {{
    const m = f.querySelector("input:checked");
    return [f.dataset.arquivo, f.dataset.grupo, m ? m.value : "", quem].join(",");
  }}));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([linhas.join("\\n")], {{type: "text/csv"}}));
  a.download = "heuristicas_inspecao_" + (quem || "anonimo") + ".csv"; a.click();
}};
</script></body></html>
"""
    saida.write_text(pagina, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exportacao", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help='pasta "Base cliente"')
    ap.add_argument("--rotulos", type=Path, default=Path("../lab-02/rotulos/rotulos.csv"))
    ap.add_argument("--saida", type=Path, default=Path("reports"))
    ap.add_argument("--limiar", type=float, required=True, help="limiar_rejeita da politica v1")
    args = ap.parse_args(argv)
    args.saida.mkdir(parents=True, exist_ok=True)

    man = ler_manifesto(args.base)
    feat = features_v0_lote(list(man.arquivo), list(man.caminho))
    exp = ler_exportacao(args.exportacao)
    ev = evidencia(exp, ler_rotulos(args.rotulos))
    tab = tabela_limiar(exp, ev)

    figura_sinais(feat, args.saida / "heuristicas_distribuicao.png")
    figura_scores(exp, args.limiar, args.saida / "heuristicas_scores.png")
    figura_limiar(tab, args.limiar, args.saida / "heuristicas_limiar.png")

    am = amostra_inspecao(exp, args.limiar)
    am.to_csv(args.saida / "heuristicas_amostra_200.csv", index_label="arquivo")
    base_rel = os.path.relpath(args.base.resolve(), args.saida.resolve())
    pagina_inspecao(am, base_rel, args.saida / "heuristicas_inspecao.html")

    rej = exp[exp.p_ruim >= args.limiar]
    ev_rej = ev[ev.p_ruim >= args.limiar]
    legiveis = ev[ev.legivel == 1].sort_values("p_ruim", ascending=False)
    resumo = {
        "fotos_base": len(feat),
        "paridade_backend_mlinsp": paridade(feat, exp),
        "leituras_com_heuristica": len(exp),
        "limiar_rejeita": args.limiar,
        "auto_rejeitadas": len(rej),
        "cobertura": round(len(rej) / len(exp), 4),
        "por_lote": {
            lote: {"leituras": len(g), "auto_rejeitadas": int((g.p_ruim >= args.limiar).sum())}
            for lote, g in exp.groupby("lote")
        },
        "evidencia": {
            "fotos": len(ev),
            "por_fonte": ev.fonte.value_counts().to_dict(),
            "legiveis": int(ev.legivel.sum()),
            "auto_rejeitadas_com_evidencia": len(ev_rej),
            "falsos_positivos": int(ev_rej.legivel.sum()),
            "maior_p_ruim_de_legivel": float(legiveis.p_ruim.iloc[0]),
            "legiveis_mais_altas": [
                {"arquivo": a, "p_ruim": round(float(r.p_ruim), 4), "classe": r.classe_max,
                 "fonte": r.fonte}
                for a, r in legiveis.head(5).iterrows()
            ],
        },
        "scores_quantis": {
            c: {f"p{int(q * 1000) / 10:g}": round(float(exp[c].quantile(q)), 4)
                for q in (0.5, 0.9, 0.95, 0.99, 0.999)}
            for c in CLASSES + ["p_ruim"]
        },
        "sinais_quantis": {
            c: {f"p{int(q * 1000) / 10:g}": round(float(feat[c].quantile(q)), 4)
                for q in (0.001, 0.01, 0.05, 0.5, 0.95, 0.99)}
            for c in SINAIS_PLANO
        },
        "latencia_ms": {
            "media": round(float(exp.latencia_ms.mean()), 2),
            "p50": float(exp.latencia_ms.quantile(0.5)),
            "p95": float(exp.latencia_ms.quantile(0.95)),
        },
        "tabela_limiar": tab.round(4).to_dict(orient="records"),
        "amostra": am.grupo.value_counts().to_dict(),
    }
    (args.saida / "heuristicas_resumo.json").write_text(
        json.dumps(resumo, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps({k: resumo[k] for k in ("auto_rejeitadas", "cobertura",
                                             "paridade_backend_mlinsp", "evidencia")},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

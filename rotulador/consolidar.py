#!/usr/bin/env python3
"""Junta os rotulos de todos e mede a concordancia (kappa de Cohen).

Copie a pasta de cada colega para rotulos/<nome>/ e rode:

    python3 consolidar.py

Saidas em saida/ (fora do git):
    todos_os_rotulos.csv       concatenacao de todos os CSVs (sem as puladas)
    rotulos_consolidados.csv   uma linha por foto, rotulo por maioria
    kappa.md                   kappa por classe e por par de rotuladores
"""

import csv
import glob
import os
from collections import Counter, defaultdict
from itertools import combinations

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_ROTULOS = os.path.join(AQUI, "rotulos")
PASTA_SAIDA = os.path.join(AQUI, "saida")
MANIFESTO = os.path.join(AQUI, "manifesto.csv")

DEFEITOS = ["reflexo", "fora_de_foco", "tampa_suja", "enquadramento", "display_apagado"]
CLASSES = ["cena"] + DEFEITOS + ["legivel"]
LIMIAR_KAPPA = 0.6


def ler_todos():
    linhas, puladas = [], 0
    for caminho in sorted(glob.glob(os.path.join(PASTA_ROTULOS, "*", "rotulos.csv"))):
        with open(caminho, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["pulada"] == "1":
                    puladas += 1
                    continue
                linhas.append(r)
    return linhas, puladas


def kappa_cohen(a, b):
    """Kappa de Cohen entre duas listas de rotulos alinhadas (mesmas fotos, mesma ordem).

    po = concordancia observada (fracao de fotos com o mesmo rotulo)
    pe = concordancia esperada ao acaso = soma, por categoria, de p_a(c) * p_b(c)
    kappa = (po - pe) / (1 - pe)
    Se pe == 1 (os dois usaram uma unica e mesma categoria em tudo) o kappa
    nao e definido: devolve None.
    """
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def maioria(valores):
    """Rotulo mais votado; empate entre os mais votados -> ('', True)."""
    contagem = Counter(valores).most_common()
    if len(contagem) > 1 and contagem[0][1] == contagem[1][1]:
        return "", True
    return contagem[0][0], False


def fmt(k):
    return "n/d" if k is None else "%.2f" % k


def main():
    linhas, puladas = ler_todos()
    if not linhas:
        raise SystemExit("Nenhum rotulo em %s/*/rotulos.csv" % PASTA_ROTULOS)
    os.makedirs(PASTA_SAIDA, exist_ok=True)

    controle = set()
    if os.path.exists(MANIFESTO):
        with open(MANIFESTO, encoding="utf-8", newline="") as f:
            controle = {r["arquivo"] for r in csv.DictReader(f) if r["controle"] == "1"}

    colunas = list(linhas[0].keys())
    with open(os.path.join(PASTA_SAIDA, "todos_os_rotulos.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)

    # foto -> rotulador -> linha
    por_foto = defaultdict(dict)
    for r in linhas:
        por_foto[r["nome_arquivo"]][r["rotulador"]] = r
    rotuladores = sorted({r["rotulador"] for r in linhas})

    # ---- consolidado por maioria ----
    consolidados = []
    for foto in sorted(por_foto):
        votos = por_foto[foto]
        saida = {"nome_arquivo": foto, "lote": next(iter(votos.values()))["lote"],
                 "n_rotuladores": len(votos), "controle": int(foto in controle)}
        algum_empate = False
        for c in CLASSES:
            valor, empate = maioria([v[c] for v in votos.values()])
            saida[c] = valor
            algum_empate |= empate
        saida["empate"] = int(algum_empate)
        consolidados.append(saida)
    with open(os.path.join(PASTA_SAIDA, "rotulos_consolidados.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["nome_arquivo", "lote", "n_rotuladores", "controle"] + CLASSES + ["empate"])
        w.writeheader()
        w.writerows(consolidados)

    # ---- kappa por par de rotuladores ----
    pares = []  # (a, b, n, {classe: kappa})
    for a, b in combinations(rotuladores, 2):
        comuns = sorted(f for f, v in por_foto.items() if a in v and b in v)
        if not comuns:
            continue
        kappas = {c: kappa_cohen([por_foto[f][a][c] for f in comuns], [por_foto[f][b][c] for f in comuns])
                  for c in CLASSES}
        pares.append((a, b, len(comuns), kappas))

    medio = {}
    for c in CLASSES:
        validos = [(n, k[c]) for _, _, n, k in pares if k[c] is not None]
        peso = sum(n for n, _ in validos)
        medio[c] = (sum(n * k for n, k in validos) / peso, peso) if peso else (None, 0)

    ctrl_2mais = sum(1 for f in controle if len(por_foto.get(f, {})) >= 2)

    md = ["# Concordancia entre rotuladores (kappa de Cohen)", "",
          "Rotuladores: %d (%s). Rotulos validos: %d. Puladas (ignoradas): %d." % (
              len(rotuladores), ", ".join(rotuladores), len(linhas), puladas),
          "Fotos de controle com 2 ou mais rotulos: %d de %d." % (ctrl_2mais, len(controle)),
          "Fotos com empate na maioria: %d de %d." % (sum(c["empate"] for c in consolidados), len(consolidados)),
          "", "## Kappa medio ponderado por n", "",
          "| classe | kappa | n (pares-foto) | alerta |", "|---|---|---|---|"]
    for c in CLASSES:
        k, n = medio[c]
        alerta = "kappa < %.1f" % LIMIAR_KAPPA if k is not None and k < LIMIAR_KAPPA else ("sem dado" if k is None else "")
        md.append("| %s | %s | %d | %s |" % (c, fmt(k), n, alerta))
    md += ["", "## Por par de rotuladores", "",
           "| par | n | " + " | ".join(CLASSES) + " |", "|---|---|" + "---|" * len(CLASSES)]
    for a, b, n, k in pares:
        md.append("| %s x %s | %d | %s |" % (a, b, n, " | ".join(fmt(k[c]) for c in CLASSES)))
    if not pares:
        md.append("| (nenhum par rotulou a mesma foto) | 0 |" + " |" * len(CLASSES))
    md += ["", "n/d = kappa indefinido (os dois usaram uma unica categoria em todas as fotos do par).", ""]
    with open(os.path.join(PASTA_SAIDA, "kappa.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n".join(md))

    # ---- distribuicoes e tempo ----
    print("## Distribuicao dos rotulos (todos os rotulos validos)\n")
    lotes = sorted({r["lote"] for r in linhas})
    print("%-18s %-16s %6s  %s" % ("classe", "valor", "total", "  ".join("%7s" % l for l in lotes)))
    for c in CLASSES:
        for valor, total in sorted(Counter(r[c] for r in linhas).items()):
            por_lote = Counter(r["lote"] for r in linhas if r[c] == valor)
            print("%-18s %-16s %6d  %s" % (c, valor, total, "  ".join("%7d" % por_lote[l] for l in lotes)))
    tempos = [float(r["segundos_na_foto"] or 0) for r in linhas]
    print("\nTempo medio por foto: %.1f s (%d rotulos)" % (sum(tempos) / len(tempos), len(tempos)))
    for nome in rotuladores:
        t = [float(r["segundos_na_foto"] or 0) for r in linhas if r["rotulador"] == nome]
        print("  %-20s %5.1f s  (%d fotos)" % (nome, sum(t) / len(t), len(t)))
    print("\nArquivos em %s" % PASTA_SAIDA)


if __name__ == "__main__":
    main()

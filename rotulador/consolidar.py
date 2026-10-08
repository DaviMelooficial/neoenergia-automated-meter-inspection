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
import hashlib
import os
from collections import Counter, defaultdict
from itertools import combinations

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_ROTULOS = os.path.join(AQUI, "rotulos")
PASTA_SAIDA = os.path.join(AQUI, "saida")
CONTROLE = os.path.join(AQUI, "controle.dat")  # sha256 dos nomes das fotos de controle

DEFEITOS = ["reflexo", "fora_de_foco", "tampa_suja", "enquadramento", "display_apagado"]
CLASSES = ["cena"] + DEFEITOS + ["legivel"]
# Cenas em que o medidor aparece: so nelas defeito e legibilidade sao uma escolha real
# (nas outras a regra do app impoe 0), entao o kappa condicionado olha so para elas.
COM_MEDIDOR = ("digital", "eletromecanico")
LIMIAR_KAPPA = 0.6


def ler_todos():
    linhas, puladas, pasta_de = [], 0, {}
    for caminho in sorted(glob.glob(os.path.join(PASTA_ROTULOS, "*", "rotulos.csv"))):
        pasta = os.path.basename(os.path.dirname(caminho))
        with open(caminho, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                # A mesma pessoa em duas pastas (ex.: zip copiado duas vezes) contaria
                # como dois rotuladores concordando consigo mesmos: inflaria o kappa.
                if pasta_de.setdefault(r["rotulador"], pasta) != pasta:
                    raise SystemExit("Rotulador '%s' aparece nas pastas '%s' e '%s'. Deixe so uma." % (
                        r["rotulador"], pasta_de[r["rotulador"]], pasta))
                if r["pulada"] == "1":
                    puladas += 1
                    continue
                linhas.append(r)
    return linhas, puladas


def aplicar_regra(saida, empates):
    """Mesma regra do app: sem medidor visivel, defeitos e legivel sao 0 (e nao empatam)."""
    if saida["cena"] == "nao_medidor":
        for d in DEFEITOS:
            saida[d] = "0"
            empates.discard(d)
    if saida["cena"] in ("nao_medidor", "ocorrencia"):
        saida["legivel"] = "0"
        empates.discard("legivel")


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

    hashes_controle = set()
    if os.path.exists(CONTROLE):
        with open(CONTROLE, encoding="ascii") as f:
            hashes_controle = {l.strip() for l in f if l.strip()}

    def eh_controle(arquivo):
        return hashlib.sha256(arquivo.encode("utf-8")).hexdigest() in hashes_controle

    # Cabeçalho fixo do esquema (não o da primeira linha lida): uma cópia modificada do app
    # com coluna extra não muda o arquivo de saída, venha ela em que ordem vier.
    colunas = ["nome_arquivo", "lote", "rotulador"] + CLASSES + ["pulada", "segundos_na_foto", "gravado_em"]
    with open(os.path.join(PASTA_SAIDA, "todos_os_rotulos.csv"), "w", encoding="utf-8", newline="") as f:
        # extrasaction="ignore": descarta colunas fora do esquema (ex.: "outros").
        w = csv.DictWriter(f, fieldnames=colunas, extrasaction="ignore")
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
                 "n_rotuladores": len(votos), "controle": int(eh_controle(foto))}
        empates = set()
        for c in CLASSES:
            valor, empate = maioria([v[c] for v in votos.values()])
            saida[c] = valor
            if empate:
                empates.add(c)
        aplicar_regra(saida, empates)
        saida["empate"] = int(bool(empates))
        consolidados.append(saida)
    with open(os.path.join(PASTA_SAIDA, "rotulos_consolidados.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["nome_arquivo", "lote", "n_rotuladores", "controle"] + CLASSES + ["empate"])
        w.writeheader()
        w.writerows(consolidados)

    # ---- kappa por par de rotuladores ----
    pares = []  # (a, b, n, {classe: kappa}, {classe: (kappa, n)} so com medidor visivel)
    for a, b in combinations(rotuladores, 2):
        comuns = sorted(f for f, v in por_foto.items() if a in v and b in v)
        if not comuns:
            continue
        kappas = {c: kappa_cohen([por_foto[f][a][c] for f in comuns], [por_foto[f][b][c] for f in comuns])
                  for c in CLASSES}
        visiveis = [f for f in comuns
                    if por_foto[f][a]["cena"] in COM_MEDIDOR and por_foto[f][b]["cena"] in COM_MEDIDOR]
        cond = {c: (kappa_cohen([por_foto[f][a][c] for f in visiveis], [por_foto[f][b][c] for f in visiveis]),
                    len(visiveis)) for c in CLASSES[1:]}
        pares.append((a, b, len(comuns), kappas, cond))

    def media_ponderada(itens):
        validos = [(n, k) for n, k in itens if k is not None]
        peso = sum(n for n, _ in validos)
        return (sum(n * k for n, k in validos) / peso, peso) if peso else (None, 0)

    medio = {c: media_ponderada([(n, k[c]) for _, _, n, k, _ in pares]) for c in CLASSES}
    medio_cond = {c: media_ponderada([cd[c][::-1] for _, _, _, _, cd in pares]) for c in CLASSES[1:]}

    ctrl_2mais = sum(1 for f in por_foto if len(por_foto[f]) >= 2 and eh_controle(f))

    md = ["# Concordancia entre rotuladores (kappa de Cohen)", "",
          "Rotuladores: %d (%s). Rotulos validos: %d. Puladas (ignoradas): %d." % (
              len(rotuladores), ", ".join(rotuladores), len(linhas), puladas),
          "Fotos de controle com 2 ou mais rotulos: %d de %d." % (ctrl_2mais, len(hashes_controle)),
          "Fotos com empate na maioria: %d de %d." % (sum(c["empate"] for c in consolidados), len(consolidados)),
          "", "## Kappa medio ponderado por n", "",
          "Colunas 'so com medidor': apenas fotos em que os dois marcaram digital ou eletromecanico.",
          "",
          "| classe | kappa | n (pares-foto) | kappa so com medidor | n so com medidor | alerta |",
          "|---|---|---|---|---|---|"]
    for c in CLASSES:
        k, n = medio[c]
        kc, nc = medio_cond.get(c, (None, 0))
        baixos = [x for x in (k, kc) if x is not None and x < LIMIAR_KAPPA]
        alerta = "kappa < %.1f" % LIMIAR_KAPPA if baixos else ("sem dado" if k is None else "")
        if c == "cena":
            md.append("| %s | %s | %d | - | - | %s |" % (c, fmt(k), n, alerta))
        else:
            md.append("| %s | %s | %d | %s | %d | %s |" % (c, fmt(k), n, fmt(kc), nc, alerta))
    md += ["", "## Por par de rotuladores", "",
           "| par | n | " + " | ".join(CLASSES) + " |", "|---|---|" + "---|" * len(CLASSES)]
    for a, b, n, k, _ in pares:
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

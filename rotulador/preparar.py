#!/usr/bin/env python3
"""Sorteia o pool de fotos para rotulagem e grava o manifesto.

Roda uma vez, na maquina do Davi, com a pasta "Base cliente" ao lado do repo:

    python3 preparar.py                 # usa ../Base cliente
    python3 preparar.py --base "/caminho/Base cliente"

Saidas (todas fora do git):
    imagens/       copia das 400 fotos sorteadas
    manifesto.csv  arquivo, lote
    controle.dat   sha256 do nome de cada foto de controle, na ordem em que entram na fila

O sorteio usa seed fixa: rodar de novo gera o mesmo pool. Se o manifesto ja
existir, o script para (os rotulos ja feitos dependem dele); use --forcar para
refazer.
"""

import argparse
import csv
import hashlib
import random
import shutil
import sys
from pathlib import Path

SEED = 20260706
FOTOS_POR_LOTE = 100
TOTAL_CONTROLE = 50

AQUI = Path(__file__).resolve().parent
BASE_PADRAO = AQUI.parent / "Base cliente"
PASTA_IMAGENS = AQUI / "imagens"
MANIFESTO = AQUI / "manifesto.csv"
CONTROLE = AQUI / "controle.dat"


def hash_nome(arquivo):
    return hashlib.sha256(arquivo.encode("utf-8")).hexdigest()


def ler_lote(pasta):
    """Devolve (com_nota, sem_nota): nomes de foto do CSV que existem no disco."""
    csvs = sorted(pasta.glob("*.csv"))
    if not csvs:
        sys.exit("Sem CSV em %s" % pasta)
    no_disco = {p.name for p in pasta.glob("*.jpg")}
    com_nota, sem_nota = set(), set()
    with open(csvs[0], encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            foto = (linha.get("Foto do medidor") or "").strip()
            if foto not in no_disco:
                continue
            nota = (linha.get("Nota de Leitura Atual") or "").strip()
            (sem_nota if nota in ("", "NA") else com_nota).add(foto)
    # Uma foto com nota em alguma linha conta como "com nota".
    sem_nota -= com_nota
    return sorted(com_nota), sorted(sem_nota)


def sortear_lote(rng, com_nota, sem_nota, n):
    """Metade com nota e metade sem; se um estrato faltar, completa com o outro."""
    k_com = min(n // 2, len(com_nota))
    k_sem = min(n - k_com, len(sem_nota))
    k_com = min(n - k_sem, len(com_nota))
    return rng.sample(com_nota, k_com) + rng.sample(sem_nota, k_sem)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, default=BASE_PADRAO, help="pasta 'Base cliente'")
    ap.add_argument("--forcar", action="store_true", help="refaz mesmo com manifesto existente")
    args = ap.parse_args()

    if MANIFESTO.exists() and not args.forcar:
        sys.exit("manifesto.csv ja existe. Use --forcar para sortear de novo "
                 "(cuidado: rotulos ja feitos referenciam o pool atual).")

    lotes = sorted(p for p in args.base.glob("PSP_EXTRATLEITIMPL_*") if p.is_dir())
    if not lotes:
        sys.exit("Nenhum lote PSP_EXTRATLEITIMPL_* em %s" % args.base)

    rng = random.Random(SEED)
    # 50 controles divididos entre os lotes: 13, 13, 12, 12 para 4 lotes.
    base_ctrl, sobra = divmod(TOTAL_CONTROLE, len(lotes))
    pool = []  # (arquivo, lote, controle, pasta_origem, tem_nota)
    for i, pasta in enumerate(lotes):
        lote = pasta.name.split("_")[2]  # PSP_EXTRATLEITIMPL_<lote>_<seq> -> <lote>
        com_nota, sem_nota = ler_lote(pasta)
        escolhidas = sortear_lote(rng, com_nota, sem_nota, FOTOS_POR_LOTE)
        n_ctrl = base_ctrl + (1 if i < sobra else 0)
        controles = set(rng.sample(escolhidas, n_ctrl))
        conj_nota = set(com_nota)
        for foto in sorted(escolhidas):
            pool.append((foto, lote, int(foto in controles), pasta, int(foto in conj_nota)))

    if PASTA_IMAGENS.exists():
        for antiga in PASTA_IMAGENS.glob("*.jpg"):
            antiga.unlink()
    PASTA_IMAGENS.mkdir(exist_ok=True)
    for foto, _, _, pasta, _ in pool:
        shutil.copy2(pasta / foto, PASTA_IMAGENS / foto)

    with open(MANIFESTO, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arquivo", "lote"])
        for foto, lote, _, _, _ in pool:
            w.writerow([foto, lote])

    # Os controles NAO vao no manifesto: a pasta e zipada para os colegas e quem
    # abrisse o CSV no Excel veria quais fotos sao controle (fim da rotulagem as
    # cegas). Gravamos so o sha256 do nome, ja na ordem fixa em que entram na
    # fila de todos; o app e o consolidar comparam hash(nome).
    controles = sorted(p[0] for p in pool if p[2])
    random.Random(SEED).shuffle(controles)
    with open(CONTROLE, "w", encoding="ascii", newline="\n") as f:
        for foto in controles:
            f.write(hash_nome(foto) + "\n")

    print("Pool: %d fotos em %s" % (len(pool), PASTA_IMAGENS))
    print("%-8s %6s %9s %9s %9s" % ("lote", "fotos", "com_nota", "sem_nota", "controle"))
    for lote in sorted({p[1] for p in pool}):
        do_lote = [p for p in pool if p[1] == lote]
        nota = sum(p[4] for p in do_lote)
        print("%-8s %6d %9d %9d %9d" % (lote, len(do_lote), nota, len(do_lote) - nota,
                                         sum(p[2] for p in do_lote)))
    print("%-8s %6d %9s %9s %9d" % ("total", len(pool), "", "", sum(p[2] for p in pool)))
    print("Manifesto: %s" % MANIFESTO)
    print("Controles (hash, ordem fixa): %s" % CONTROLE)


if __name__ == "__main__":
    main()

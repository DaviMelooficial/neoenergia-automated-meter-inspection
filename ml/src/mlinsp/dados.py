"""Manifesto da base do cliente e leitura dos rotulos humanos.

Uma linha por foto, lida direto de "Base cliente/" (sem depender do banco):
arquivo, caminho, lote, id_externo, seq, numero_medidor, nota, orfa.

O nome da foto e o CSV seguem o contrato verificado em
app/backend/src/inspecao/ingest/parser.py; aqui so o necessario para o Lab 2.
"""

from __future__ import annotations

import csv
import importlib.util
import re
from pathlib import Path

import numpy as np
import pandas as pd

CENAS = ["digital", "eletromecanico", "ocorrencia", "nao_medidor"]
COM_MEDIDOR = ("digital", "eletromecanico")
DEFEITOS = ["reflexo", "fora_de_foco", "tampa_suja", "enquadramento", "display_apagado"]

PADRAO_FOTO = re.compile(
    r"^PSP_EXTRATLEITIMPL_(?P<data>\d{6})_(?P<hora>\d{4})_(?P<id>\d{20})_(?P<seq>\d{3})\.jpg$"
)
PADRAO_PASTA = re.compile(r"^PSP_EXTRATLEITIMPL_(?P<data>\d{6})_\d{4}$")
VAZIOS = {"", "NA"}


def _limpar(valor: str | None) -> str | None:
    v = (valor or "").strip()
    return None if v in VAZIOS else v


def _ler_csv_lote(pasta: Path) -> dict[str, dict]:
    """foto -> {medidores, nota}. Uma foto pode aparecer em mais de uma linha."""
    por_foto: dict[str, dict] = {}
    for caminho in sorted(pasta.glob("*.csv")):
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            for linha in csv.DictReader(f, delimiter=";"):
                foto = _limpar(linha.get("Foto do medidor"))
                if foto is None:
                    continue
                med = _limpar(linha.get("Numero do medidor"))
                medidores = [m.strip() for m in med.split("/") if m.strip()] if med else []
                item = por_foto.setdefault(foto, {"medidores": [], "notas": []})
                item["medidores"] += [m for m in medidores if m not in item["medidores"]]
                nota = _limpar(linha.get("Nota de Leitura Atual"))
                if nota and nota not in item["notas"]:
                    item["notas"].append(nota)
    return por_foto


def ler_manifesto(base: Path) -> pd.DataFrame:
    """Lista todas as fotos dos lotes em `base`, cruzadas com o CSV do proprio lote.

    numero_medidor: medidores separados por "/" (uma linha pode ter ate 8); None se a
    foto nao esta no CSV (orfa). `medidores` guarda a lista, para checar vazamento.
    """
    base = Path(base)
    linhas = []
    for pasta in sorted(p for p in base.iterdir() if p.is_dir() and PADRAO_PASTA.match(p.name)):
        lote = PADRAO_PASTA.match(pasta.name).group("data")
        csv_lote = _ler_csv_lote(pasta)
        for foto in sorted(pasta.glob("*.jpg")):
            m = PADRAO_FOTO.match(foto.name)
            if m is None:
                continue
            info = csv_lote.get(foto.name)
            medidores = info["medidores"] if info else []
            linhas.append({
                "arquivo": foto.name,
                "caminho": str(foto),
                "lote": lote,
                "id_externo": m.group("id"),
                "seq": int(m.group("seq")),
                "numero_medidor": "/".join(medidores) if medidores else None,
                "medidores": medidores,
                "nota": "/".join(info["notas"]) if info and info["notas"] else None,
                "orfa": info is None,
            })
    if not linhas:
        raise FileNotFoundError(f"nenhuma foto PSP_EXTRATLEITIMPL_* encontrada em {base}")
    return pd.DataFrame(linhas)


def medidores_do_csv(base: Path) -> dict[str, set[str]]:
    """lote -> medidores de TODAS as linhas do CSV, com ou sem foto (a conta do guia)."""
    saida: dict[str, set[str]] = {}
    for pasta in sorted(p for p in Path(base).iterdir() if p.is_dir() and PADRAO_PASTA.match(p.name)):
        lote = PADRAO_PASTA.match(pasta.name).group("data")
        meds: set[str] = set()
        for caminho in pasta.glob("*.csv"):
            with open(caminho, encoding="utf-8-sig", newline="") as f:
                for linha in csv.DictReader(f, delimiter=";"):
                    med = _limpar(linha.get("Numero do medidor"))
                    if med:
                        meds |= {m.strip() for m in med.split("/") if m.strip()}
        saida[lote] = meds
    return saida


def _para_int(serie: pd.Series) -> pd.Series:
    """'' (empate na maioria) e ausente viram NA; o resto vira 0/1."""
    return pd.to_numeric(serie.replace("", np.nan), errors="coerce").astype("Int64")


def ler_rotulos(rotulos_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Le a saida do rotulador/consolidar.py.

    Devolve (consolidados, todos). `consolidados` tem uma linha por foto (maioria);
    campos empatados chegam vazios e viram NA (a foto sai so daquela tarefa).
    `todos` tem uma linha por (foto, rotulador) e serve para o kappa; None se ausente.
    """
    rotulos_dir = Path(rotulos_dir)
    arq = rotulos_dir / "rotulos_consolidados.csv"
    if not arq.exists():
        raise FileNotFoundError(
            f"{arq} nao existe. Rode `python3 rotulador/consolidar.py` depois de copiar os "
            "rotulos de cada pessoa para rotulador/rotulos/<nome>/."
        )
    cons = pd.read_csv(arq, dtype=str, keep_default_na=False)
    cons["cena"] = cons["cena"].where(cons["cena"].isin(CENAS))
    for c in DEFEITOS + ["legivel", "n_rotuladores", "controle", "empate"]:
        if c in cons:
            cons[c] = _para_int(cons[c])
    todos = None
    arq_todos = rotulos_dir / "todos_os_rotulos.csv"
    if arq_todos.exists():
        todos = pd.read_csv(arq_todos, dtype=str, keep_default_na=False)
    return cons, todos


def carregar_consolidar(repo: Path):
    """Importa rotulador/consolidar.py (kappa_cohen, CLASSES...) sem duplicar o codigo."""
    caminho = Path(repo) / "rotulador" / "consolidar.py"
    spec = importlib.util.spec_from_file_location("consolidar", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def kappa_por_classe(todos: pd.DataFrame, kappa_fn, classes: list[str],
                     limiar: float = 0.6) -> pd.DataFrame:
    """Kappa de Cohen por classe, media ponderada por n sobre os pares de rotuladores.

    Mesmo calculo do rotulador/consolidar.py (que gera o kappa.md); a coluna
    `kappa_com_medidor` usa so as fotos em que os dois viram digital/eletromecanico,
    porque fora delas a regra do app impoe defeito e legivel = 0.
    """
    por_foto: dict[str, dict[str, dict]] = {}
    for r in todos.to_dict("records"):
        por_foto.setdefault(r["nome_arquivo"], {})[r["rotulador"]] = r
    rotuladores = sorted({r["rotulador"] for r in todos.to_dict("records")})
    acum = {c: [] for c in classes}
    acum_med = {c: [] for c in classes}
    for i, a in enumerate(rotuladores):
        for b in rotuladores[i + 1:]:
            comuns = sorted(f for f, v in por_foto.items() if a in v and b in v)
            if not comuns:
                continue
            vis = [f for f in comuns if por_foto[f][a]["cena"] in COM_MEDIDOR
                   and por_foto[f][b]["cena"] in COM_MEDIDOR]
            for c in classes:
                k = kappa_fn([por_foto[f][a][c] for f in comuns], [por_foto[f][b][c] for f in comuns])
                acum[c].append((len(comuns), k))
                if c != "cena" and vis:
                    km = kappa_fn([por_foto[f][a][c] for f in vis], [por_foto[f][b][c] for f in vis])
                    acum_med[c].append((len(vis), km))

    def media(itens):
        val = [(n, k) for n, k in itens if k is not None]
        peso = sum(n for n, _ in val)
        return (sum(n * k for n, k in val) / peso if peso else None), sum(n for n, _ in itens)

    linhas = []
    for c in classes:
        k, n = media(acum[c])
        km, nm = media(acum_med[c]) if c != "cena" else (None, 0)
        baixos = [x for x in (k, km) if x is not None and x < limiar]
        linhas.append({"classe": c, "kappa": k, "n_pares_foto": n,
                       "kappa_com_medidor": km, "n_com_medidor": nm,
                       "alerta": f"kappa < {limiar}" if baixos else ("n/d" if k is None else "")})
    return pd.DataFrame(linhas)


def carregar_imagem(caminho: str | Path) -> np.ndarray:
    """JPEG -> RGB uint8 (H, W, 3)."""
    import cv2

    img = cv2.imread(str(caminho), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"imagem invalida: {caminho}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

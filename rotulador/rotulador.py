#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Rotulador de fotos de medidor (Korvian, Lab 2).

Servidor local, so biblioteca padrao. Uso:

    python3 rotulador.py            # porta 8765 (ou a proxima livre)
    python3 rotulador.py 9000       # outra porta
    python3 rotulador.py --sem-navegador

Escuta apenas em 127.0.0.1. Os rotulos de cada pessoa ficam em
rotulos/<nome>/rotulos.csv e sao gravados a cada foto.
"""

import csv
import hashlib
import json
import os
import random
import sys
import threading
import unicodedata
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_IMAGENS = os.path.join(AQUI, "imagens")
PASTA_ROTULOS = os.path.join(AQUI, "rotulos")
MANIFESTO = os.path.join(AQUI, "manifesto.csv")
PAGINA = os.path.join(AQUI, "pagina.html")

PORTA_PADRAO = 8765
TENTATIVAS_PORTA = 20
# Uma foto de controle a cada N posicoes da fila de cada pessoa.
INTERVALO_CONTROLE = 4

CENAS = ("digital", "eletromecanico", "ocorrencia", "nao_medidor")
DEFEITOS = ("reflexo", "fora_de_foco", "tampa_suja", "enquadramento", "display_apagado")
COLUNAS = (["nome_arquivo", "lote", "rotulador", "cena"] + list(DEFEITOS)
           + ["legivel", "pulada", "segundos_na_foto", "gravado_em"])

TRAVA = threading.Lock()


def normalizar_nome(nome):
    """'Joao da Silva' -> 'joao_da_silva' (minusculas, sem acento, so [a-z0-9_])."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    limpo = "".join(c if c.isalnum() else "_" for c in sem_acento.lower().strip())
    while "__" in limpo:
        limpo = limpo.replace("__", "_")
    return limpo.strip("_")[:40]


def carregar_manifesto():
    if not os.path.exists(MANIFESTO):
        return []
    with open(MANIFESTO, encoding="utf-8", newline="") as f:
        return [{"arquivo": r["arquivo"], "lote": r["lote"], "controle": r["controle"] == "1"}
                for r in csv.DictReader(f)]


POOL = carregar_manifesto()
POR_ARQUIVO = {p["arquivo"]: p for p in POOL}


def ordem_da_pessoa(nome):
    """Fila deterministica por nome: controles intercalados (1 a cada 4), depois o resto.

    A seed vem do nome, entao a mesma pessoa ve sempre a mesma ordem e pessoas
    diferentes veem ordens diferentes. Quem fizer as primeiras ~200 fotos passa
    por todos os 50 controles.
    """
    rng = random.Random(int(hashlib.sha256(nome.encode()).hexdigest(), 16))
    controles = sorted(p["arquivo"] for p in POOL if p["controle"])
    demais = sorted(p["arquivo"] for p in POOL if not p["controle"])
    rng.shuffle(controles)
    rng.shuffle(demais)
    ordem = []
    while controles or demais:
        if controles and (len(ordem) % INTERVALO_CONTROLE == INTERVALO_CONTROLE - 1 or not demais):
            ordem.append(controles.pop())
        else:
            ordem.append(demais.pop())
    return ordem


def arquivo_csv(nome):
    return os.path.join(PASTA_ROTULOS, nome, "rotulos.csv")


def ler_rotulos(nome):
    caminho = arquivo_csv(nome)
    if not os.path.exists(caminho):
        return {}
    with open(caminho, encoding="utf-8", newline="") as f:
        return {r["nome_arquivo"]: r for r in csv.DictReader(f)}


def gravar_rotulos(nome, rotulos):
    """Regrava o CSV inteiro de forma atomica (arquivo temporario + replace)."""
    pasta = os.path.join(PASTA_ROTULOS, nome)
    os.makedirs(pasta, exist_ok=True)
    caminho = arquivo_csv(nome)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        w.writeheader()
        for linha in rotulos.values():
            w.writerow(linha)
    os.replace(tmp, caminho)


def estado(nome):
    feitos = ler_rotulos(nome)
    return {"nome": nome, "feitas": len(feitos), "total": len(POOL),
            "restantes": sum(1 for a in POOL if a["arquivo"] not in feitos),
            "pasta": os.path.join(PASTA_ROTULOS, nome)}


def proxima(nome):
    feitos = ler_rotulos(nome)
    for arquivo in ordem_da_pessoa(nome):
        if arquivo not in feitos:
            return arquivo
    return None


def montar_linha(nome, dados):
    """Valida o rotulo vindo da pagina e aplica as regras de consistencia."""
    arquivo = str(dados.get("arquivo", ""))
    if arquivo not in POR_ARQUIVO:
        raise ValueError("foto desconhecida")
    try:
        segundos = max(0.0, round(float(dados.get("segundos", 0)), 1))
    except (TypeError, ValueError):
        segundos = 0.0
    linha = {"nome_arquivo": arquivo, "lote": POR_ARQUIVO[arquivo]["lote"], "rotulador": nome,
             "segundos_na_foto": segundos,
             "gravado_em": datetime.now().isoformat(timespec="seconds")}
    if dados.get("pulada"):
        linha.update({"cena": "", "legivel": "", "pulada": 1})
        linha.update({d: "" for d in DEFEITOS})
        return linha
    cena = dados.get("cena")
    if cena not in CENAS:
        raise ValueError("cena obrigatoria")
    legivel = dados.get("legivel")
    if legivel not in (0, 1):
        raise ValueError("legivel obrigatorio")
    defeitos = dados.get("defeitos") or {}
    linha.update({d: 1 if defeitos.get(d) else 0 for d in DEFEITOS})
    if cena == "nao_medidor":
        linha.update({d: 0 for d in DEFEITOS})
        legivel = 0
    elif cena == "ocorrencia":
        legivel = 0
    linha.update({"cena": cena, "legivel": legivel, "pulada": 0})
    return linha


def rotulo_para_pagina(linha):
    """Converte a linha do CSV no formato que a pagina usa para preencher os botoes."""
    if linha is None:
        return None
    return {"cena": linha["cena"] or None,
            "defeitos": {d: linha[d] == "1" for d in DEFEITOS},
            "legivel": int(linha["legivel"]) if linha["legivel"] in ("0", "1") else None,
            "pulada": linha["pulada"] == "1",
            "segundos": float(linha["segundos_na_foto"] or 0)}


class Servidor(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "Rotulador/1.0"

    def log_message(self, *args):
        pass  # sem log por requisicao; o terminal fica limpo para quem nao e tecnico

    # -- respostas ---------------------------------------------------------
    def responder(self, status, corpo, tipo):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def json(self, obj, status=200):
        self.responder(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    def erro(self, status, msg):
        self.json({"erro": msg}, status)

    def nome_da_query(self, query):
        nome = normalizar_nome(query.get("nome", [""])[0])
        if not nome:
            raise ValueError("nome obrigatorio")
        return nome

    # -- rotas -------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                with open(PAGINA, "rb") as f:
                    return self.responder(200, f.read(), "text/html; charset=utf-8")
            if url.path.startswith("/img/"):
                return self.servir_imagem(unquote(url.path[len("/img/"):]))
            if url.path == "/api/config":
                return self.json({"total": len(POOL), "pronto": bool(POOL)})
            if url.path == "/api/estado":
                return self.json(estado(self.nome_da_query(query)))
            if url.path == "/api/proxima":
                nome = self.nome_da_query(query)
                return self.json({"arquivo": proxima(nome), **estado(nome)})
            if url.path == "/api/rotulo":
                nome = self.nome_da_query(query)
                arquivo = query.get("arquivo", [""])[0]
                return self.json({"rotulo": rotulo_para_pagina(ler_rotulos(nome).get(arquivo))})
        except ValueError as e:
            return self.erro(400, str(e))
        self.erro(404, "nao encontrado")

    def do_POST(self):
        url = urlparse(self.path)
        try:
            tamanho = int(self.headers.get("Content-Length", 0))
            if tamanho > 100000:
                return self.erro(413, "corpo grande demais")
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
            nome = normalizar_nome(str(dados.get("nome", "")))
            if not nome:
                raise ValueError("nome obrigatorio")
            if url.path == "/api/sessao":
                with TRAVA:
                    if not os.path.exists(arquivo_csv(nome)):
                        gravar_rotulos(nome, {})
                return self.json(estado(nome))
            if url.path == "/api/rotulo":
                linha = montar_linha(nome, dados)
                with TRAVA:
                    rotulos = ler_rotulos(nome)
                    rotulos[linha["nome_arquivo"]] = linha
                    gravar_rotulos(nome, rotulos)
                return self.json({"ok": True, **estado(nome)})
        except ValueError as e:
            return self.erro(400, str(e))
        self.erro(404, "nao encontrado")

    def servir_imagem(self, nome_arquivo):
        # So nomes do manifesto: nada de "../" nem caminhos arbitrarios.
        if nome_arquivo not in POR_ARQUIVO:
            return self.erro(404, "imagem nao encontrada")
        caminho = os.path.join(PASTA_IMAGENS, nome_arquivo)
        if os.path.dirname(os.path.realpath(caminho)) != os.path.realpath(PASTA_IMAGENS):
            return self.erro(404, "imagem nao encontrada")
        try:
            with open(caminho, "rb") as f:
                corpo = f.read()
        except OSError:
            return self.erro(404, "imagem nao encontrada")
        self.responder(200, corpo, "image/jpeg")


def subir(porta_inicial):
    for porta in range(porta_inicial, porta_inicial + TENTATIVAS_PORTA):
        try:
            return Servidor(("127.0.0.1", porta), Handler), porta
        except OSError:
            continue
    sys.exit("Nenhuma porta livre entre %d e %d." % (porta_inicial, porta_inicial + TENTATIVAS_PORTA - 1))


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    abrir = "--sem-navegador" not in sys.argv
    porta_inicial = int(args[0]) if args else PORTA_PADRAO

    if not POOL:
        print("ATENCAO: nao achei o manifesto.csv com as fotos. Peca a pasta completa ao Davi.")
    servidor, porta = subir(porta_inicial)
    endereco = "http://localhost:%d" % porta
    print("Rotulador rodando em %s" % endereco)
    print("Deixe esta janela aberta enquanto rotula. Para sair: feche a janela ou Ctrl+C.")
    if abrir:
        threading.Timer(0.8, webbrowser.open, args=(endereco,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado. Seus rotulos ja estao salvos.")


if __name__ == "__main__":
    main()

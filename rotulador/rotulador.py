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
CONTROLE = os.path.join(AQUI, "controle.dat")
PAGINA = os.path.join(AQUI, "pagina.html")

PORTA_PADRAO = 8765
TENTATIVAS_PORTA = 20
# Controle pulado volta depois de pelo menos N outras fotos (uma vez so).
ESPERA_CONTROLE_PULADO = 3

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


def hash_nome(arquivo):
    return hashlib.sha256(arquivo.encode("utf-8")).hexdigest()


def carregar_manifesto():
    if not os.path.exists(MANIFESTO):
        return []
    with open(MANIFESTO, encoding="utf-8", newline="") as f:
        return [{"arquivo": r["arquivo"], "lote": r["lote"]} for r in csv.DictReader(f)]


def carregar_controles():
    """Fotos de controle na ordem fixa, igual para todas as pessoas.

    A lista vem de controle.dat como sha256 do nome do arquivo (gerado pelo
    preparar.py), e nao como coluna do manifesto: a pasta vai zipada para os
    colegas e o manifesto aberto no Excel revelaria quais fotos sao controle.
    A ordem fixa faz quem rotula poucas fotos ver os mesmos primeiros controles
    que os colegas, maximizando os pares para o kappa.
    """
    if not os.path.exists(CONTROLE):
        return []
    por_hash = {hash_nome(p["arquivo"]): p["arquivo"] for p in POOL}
    with open(CONTROLE, encoding="ascii") as f:
        return [por_hash[h] for h in (linha.strip() for linha in f) if h in por_hash]


POOL = carregar_manifesto()
POR_ARQUIVO = {p["arquivo"]: p for p in POOL}
ORDEM_CONTROLES = carregar_controles()
CONTROLES = set(ORDEM_CONTROLES)

# Controles pulados uma vez nesta execucao do app: nome -> {arquivo: posicao em que pulou}.
# Nao vao para o CSV; voltam na fila depois de ESPERA_CONTROLE_PULADO fotos.
ADIADOS = {}
# Controles que ja foram adiados uma vez: o segundo X grava como pulada.
JA_ADIADOS = {}


def ordem_demais(nome):
    """Fotos que nao sao de controle, em ordem propria de cada pessoa (seed do nome)."""
    rng = random.Random(int(hashlib.sha256(nome.encode()).hexdigest(), 16))
    demais = sorted(p["arquivo"] for p in POOL if p["arquivo"] not in CONTROLES)
    rng.shuffle(demais)
    return demais


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
    # Controle adiado conta como feito na tela, para o contador nao denunciar o controle.
    vistos = set(feitos) | set(ADIADOS.get(nome, {}))
    return {"nome": nome, "feitas": len(vistos), "total": len(POOL),
            "restantes": len(POOL) - len(vistos),
            "pasta": os.path.join(PASTA_ROTULOS, nome)}


def proxima(nome):
    """Proxima foto da pessoa: alterna nao-controle e controle (posicoes 2, 4, 6...).

    Os controles saem na ordem fixa ORDEM_CONTROLES ate a pessoa ter visto os 50;
    as demais saem na ordem da pessoa. Um controle pulado uma vez volta no lugar de
    uma nao-controle depois de ESPERA_CONTROLE_PULADO fotos.
    """
    feitos = ler_rotulos(nome)
    adiados = ADIADOS.get(nome, {})
    processadas = len(feitos) + len(adiados)
    n_controle = sum(1 for a in feitos if a in CONTROLES) + len(adiados)
    n_demais = len(feitos) - sum(1 for a in feitos if a in CONTROLES)

    controles = [a for a in ORDEM_CONTROLES if a not in feitos and a not in adiados]
    demais = [a for a in ordem_demais(nome) if a not in feitos]
    maduros = [a for a, pos in adiados.items() if processadas >= pos + ESPERA_CONTROLE_PULADO]

    if controles and n_demais > n_controle:
        return controles[0]
    if maduros:
        return maduros[0]
    if demais:
        return demais[0]
    if controles:
        return controles[0]
    return next(iter(adiados), None)


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
    if type(legivel) is not int or legivel not in (0, 1):
        raise ValueError("legivel obrigatorio (0 ou 1)")
    defeitos = dados.get("defeitos") or {}
    if not isinstance(defeitos, dict):
        raise ValueError("defeitos invalidos")
    for d in DEFEITOS:
        valor = defeitos.get(d, 0)
        if type(valor) is not int or valor not in (0, 1):
            raise ValueError("defeito %s deve ser 0 ou 1" % d)
        linha[d] = valor
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
            # Outro site aberto no navegador nao pode gravar aqui (so a propria pagina).
            origem = self.headers.get("Origin")
            porta = self.server.server_address[1]
            if origem and origem not in ("http://127.0.0.1:%d" % porta, "http://localhost:%d" % porta):
                return self.erro(403, "origem nao permitida")
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
            if not isinstance(dados, dict):
                raise ValueError("corpo deve ser um objeto JSON")
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
                arquivo = linha["nome_arquivo"]
                with TRAVA:
                    rotulos = ler_rotulos(nome)
                    adiados = ADIADOS.setdefault(nome, {})
                    ja = JA_ADIADOS.setdefault(nome, set())
                    if (linha["pulada"] == 1 and arquivo in CONTROLES
                            and arquivo not in rotulos and arquivo not in ja):
                        # Primeiro X num controle: nao grava, volta mais adiante. Guarda
                        # quantas fotos ja tinham passado contando com esta.
                        ja.add(arquivo)
                        adiados[arquivo] = len(rotulos) + len(adiados) + 1
                    else:
                        rotulos[arquivo] = linha
                        gravar_rotulos(nome, rotulos)
                        adiados.pop(arquivo, None)
                return self.json({"ok": True, **estado(nome)})
        except ValueError as e:
            return self.erro(400, str(e))
        except OSError:
            # No Windows o Excel trava o arquivo aberto e o os.replace falha.
            return self.erro(409, "Nao consegui gravar o rotulos.csv. Feche o arquivo rotulos.csv "
                                  "(ex.: Excel) e tente de novo.")
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

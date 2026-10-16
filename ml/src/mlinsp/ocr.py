"""Spike de OCR (marco M9): ler o display do medidor em 360x480 e medir contra o gabarito.

Tres abordagens locais, sem nuvem (plano 04, secao 8):
  tesseract_centro   Tesseract com whitelist de digitos sobre a regiao central recortada
  easyocr            EasyOCR (deteccao CRAFT + reconhecimento) na foto inteira
  recorte_tesseract  recorte manual do display (caixa do gabarito) seguido de Tesseract
mais `recorte_easyocr` (mesmo recorte, reconhecimento do EasyOCR), que separa o erro de
deteccao do erro de leitura tambem para o EasyOCR.

As metricas comparam a parte inteira da leitura, sem zeros a esquerda: o leiturista registra
`2961` para um display que mostra `002961.5`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import cv2
import numpy as np
import pandas as pd

WHITELIST = "0123456789."
# O display mostra a leitura com zeros a esquerda (4 a 6 posicoes no ciclometro e no LCD);
# a tensao '240' tem 3 digitos e o numero de serie impresso tem 10.
MIN_DIGITOS, MAX_DIGITOS = 4, 7


def normalizar_leitura(texto: str | None) -> str:
    """Parte inteira so com digitos e sem zeros a esquerda. '' se nao houver digito.

    '002961.5' -> '2961'; '0000' -> '0'; 'abc' -> ''.
    """
    if not texto:
        return ""
    inteiro = re.split(r"[.,]", str(texto).strip(), maxsplit=1)[0]
    digitos = re.sub(r"\D", "", inteiro)
    if not digitos:
        return ""
    return digitos.lstrip("0") or "0"


def distancia_edicao(a: str, b: str) -> int:
    """Levenshtein (insercao, remocao e troca com custo 1)."""
    if len(a) < len(b):
        a, b = b, a
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        atual = [i]
        for j, cb in enumerate(b, start=1):
            atual.append(min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ca != cb)))
        anterior = atual
    return anterior[-1]


def comparar(previsto: str | None, gabarito: str) -> dict:
    """Acerto exato, acerto dos ultimos 4 digitos e distancia de edicao (ja normalizados).

    Ultimos 4: os dois lados completados com zero a esquerda ate 4 digitos, entao para
    leitura curta equivale ao exato. Sem previsao, nada acerta e a distancia e len(gabarito).
    """
    g = normalizar_leitura(gabarito)
    if not g:
        raise ValueError(f"gabarito sem digitos: {gabarito!r}")
    p = normalizar_leitura(previsto)
    return {
        "previsto_norm": p,
        "exato": p == g,
        "ultimos4": bool(p) and p.zfill(4)[-4:] == g.zfill(4)[-4:],
        "distancia": distancia_edicao(p, g),
        # display digital com a casa decimal lida como inteira: '29615' para '2961'
        "digito_a_mais": len(p) == len(g) + 1 and p.startswith(g),
    }


def wilson(acertos: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson (95%) para uma proporcao."""
    if n == 0:
        return (math.nan, math.nan)
    p = acertos / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centro - meia), min(1.0, centro + meia))


def resumir(resultados: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Agrega o resultado por foto (colunas de `comparar` + `segundos`) por abordagem.

    `por`: colunas de agrupamento alem de `abordagem` (ex.: ['cena']).
    """
    chaves = ["abordagem"] + (por or [])
    linhas = []
    for chave, g in resultados.groupby(chaves, sort=False):
        chave = chave if isinstance(chave, tuple) else (chave,)
        n, acertos = len(g), int(g["exato"].sum())
        lo, hi = wilson(acertos, n)
        linhas.append({
            **dict(zip(chaves, chave)),
            "n": n,
            "acerto_exato": acertos / n,
            "ic95_inf": lo,
            "ic95_sup": hi,
            "acerto_ultimos4": float(g["ultimos4"].mean()),
            "distancia_media": float(g["distancia"].mean()),
            "sem_leitura": float((g["previsto_norm"] == "").mean()),
            "digito_a_mais": float(g["digito_a_mais"].mean()),
            "segundos_p50": float(g["segundos"].median()),
            "segundos_p95": float(g["segundos"].quantile(0.95)),
        })
    return pd.DataFrame(linhas)


@dataclass(slots=True)
class Candidato:
    texto: str
    confianca: float  # 0..1
    altura: float = 0.0  # altura da caixa do texto, em px


def _digitos_inteiros(texto: str) -> str:
    return re.sub(r"\D", "", re.split(r"[.,]", texto.strip(), maxsplit=1)[0])


def escolher_leitura(candidatos: list[Candidato]) -> str:
    """Escolhe, entre os textos achados na foto, o que tem cara de leitura.

    So entram textos com MIN_DIGITOS..MAX_DIGITOS digitos na parte inteira (sai a tensao
    '240' e o numero de serie de 10 digitos). Vence o texto mais alto: os digitos da
    leitura sao os maiores do mostrador (o codigo '03' do LCD e menor). Empate vai para a
    maior confianca. '' se nenhum servir.

    Altura em vez de confianca: nas 98 fotos conferidas do M9 a regra por altura acertou
    10 contra 5 da regra por confianca no EasyOCR da foto inteira.
    """
    validos = [c for c in candidatos
               if MIN_DIGITOS <= len(_digitos_inteiros(c.texto)) <= MAX_DIGITOS]
    if not validos:
        return ""
    return max(validos, key=lambda c: (c.altura, c.confianca)).texto


# ---------------------------------------------------------------------------
# Recortes e preprocessamento
# ---------------------------------------------------------------------------

def recorte_central(img: np.ndarray, frac: float = 0.8) -> np.ndarray:
    """Mantem a fracao `frac` central da largura e da altura."""
    h, w = img.shape[:2]
    dh, dw = round(h * (1 - frac) / 2), round(w * (1 - frac) / 2)
    return img[dh:h - dh, dw:w - dw]


def recortar_caixa(img: np.ndarray, caixa: tuple[int, int, int, int],
                   margem: float = 0.1) -> np.ndarray:
    """Recorta (x0, y0, x1, y1) com margem proporcional, limitado a imagem."""
    x0, y0, x1, y1 = caixa
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"caixa invalida: {caixa}")
    h, w = img.shape[:2]
    mx, my = round((x1 - x0) * margem), round((y1 - y0) * margem)
    return img[max(0, y0 - my):min(h, y1 + my), max(0, x0 - mx):min(w, x1 + mx)]


def ler_caixa(texto: str | float | None) -> tuple[int, int, int, int] | None:
    """'x0,y0,x1,y1' do gabarito -> tupla; None se vazio."""
    if texto is None or (isinstance(texto, float) and math.isnan(texto)) or not str(texto).strip():
        return None
    partes = [int(p) for p in str(texto).split(",")]
    if len(partes) != 4:
        raise ValueError(f"caixa precisa de 4 inteiros: {texto!r}")
    return tuple(partes)  # type: ignore[return-value]


def preparar_tesseract(rgb: np.ndarray, altura_alvo: int) -> np.ndarray:
    """Cinza, amplia ate `altura_alvo`, CLAHE e Otsu. Saida binaria uint8 (texto escuro)."""
    cinza = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    escala = max(1.0, altura_alvo / cinza.shape[0])
    if escala > 1.0:
        cinza = cv2.resize(cinza, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    cinza = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(cinza)
    cinza = cv2.GaussianBlur(cinza, (3, 3), 0)
    _, binaria = cv2.threshold(cinza, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Tesseract espera texto escuro em fundo claro: se a maior parte e escura, inverte.
    if (binaria == 0).mean() > 0.5:
        binaria = 255 - binaria
    return binaria


# ---------------------------------------------------------------------------
# Motores
# ---------------------------------------------------------------------------

def tesseract_candidatos(img: np.ndarray, psm: int) -> list[Candidato]:
    """Palavras do Tesseract (image_to_data) com confianca e altura, whitelist de digitos."""
    import pytesseract

    config = f"--oem 1 --psm {psm} -c tessedit_char_whitelist={WHITELIST}"
    dados = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
    saida = []
    for texto, conf, altura in zip(dados["text"], dados["conf"], dados["height"]):
        texto = (texto or "").strip()
        conf = float(conf)
        if texto and conf >= 0:
            saida.append(Candidato(texto, conf / 100.0, float(altura)))
    return saida


def ler_tesseract_centro(rgb: np.ndarray) -> str:
    """Abordagem 1: regiao central, Tesseract em modo texto esparso (psm 11)."""
    centro = recorte_central(rgb)
    img = preparar_tesseract(centro, altura_alvo=3 * centro.shape[0])
    return escolher_leitura(tesseract_candidatos(img, psm=11))


def ler_recorte_tesseract(rgb: np.ndarray, caixa: tuple[int, int, int, int]) -> str:
    """Abordagem 3: recorte manual do display seguido de Tesseract.

    Tenta linha unica (psm 7) e texto esparso (psm 11), na polaridade preparada e na
    invertida (LCD tem digito escuro, ciclometro tem digito claro), e aplica
    `escolher_leitura` sobre todos os candidatos.
    """
    img = preparar_tesseract(recortar_caixa(rgb, caixa), altura_alvo=120)
    candidatos = []
    for variante in (img, 255 - img):
        for psm in (7, 11):
            candidatos += tesseract_candidatos(variante, psm=psm)
    return escolher_leitura(candidatos)


class LeitorEasyOCR:
    """Abordagens 2 e 4. Carrega o modelo uma vez (baixa os pesos na primeira execucao)."""

    def __init__(self, gpu: bool = False):
        import easyocr

        self.reader = easyocr.Reader(["en"], gpu=gpu, verbose=False)

    def candidatos(self, rgb: np.ndarray) -> list[Candidato]:
        achados = self.reader.readtext(rgb, allowlist=WHITELIST, detail=1)
        return [Candidato(str(texto), float(conf), _altura_caixa(caixa))
                for caixa, texto, conf in achados]

    def ler_foto(self, rgb: np.ndarray) -> str:
        return escolher_leitura(self.candidatos(rgb))

    def ler_recorte(self, rgb: np.ndarray, caixa: tuple[int, int, int, int]) -> str:
        """Recorte manual ampliado ate 64 px de altura; mesma escolha da foto inteira."""
        recorte = recortar_caixa(rgb, caixa)
        escala = max(1.0, 64 / recorte.shape[0])
        if escala > 1.0:
            recorte = cv2.resize(recorte, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
        return escolher_leitura(self.candidatos(recorte))


def _altura_caixa(pontos) -> float:
    ys = [float(p[1]) for p in pontos]
    return max(ys) - min(ys)

"""Sinais classicos de qualidade (marco M5), sem treino. OpenCV puro.

Porte de `ml/src/mlinsp/heuristicas.py` (features V0 do Lab 2): os calculos sao
os mesmos, linha a linha, para que o numero visto no notebook seja o numero que
o backend grava. O backend nao importa o pacote `ml` (ambientes separados); a
paridade e garantida por `tests/test_heuristics.py`, contra valores gerados pelo
`mlinsp`.

Unica diferenca de interface: o `mlinsp` recebe RGB, aqui a imagem chega BGR
(saida de `cv2.imdecode`). As conversoes para cinza e HSV usam a constante do
espaco de origem, entao o resultado e identico.

Os 5 sinais do plano 04, secao 2, e o nome V0 de cada um:

| sinal do plano    | feature V0        |
| nitidez           | lap_var           |
| glare_ratio       | frac_estourados   |
| escuridao         | frac_escuros      |
| contraste         | contraste         |
| entropia_bordas   | bordas_centro     |
"""

import cv2
import numpy as np

NOMES_V0 = (
    "lap_var",
    "lap_var_centro",
    "tenengrad",
    "brilho_media",
    "contraste",
    "saturacao_media",
    "frac_estourados",
    "frac_escuros",
    "entropia_hist",
    "bordas_centro",
    "p01",
    "p99",
)


def _centro(img: np.ndarray, frac: float = 0.5) -> np.ndarray:
    h, w = img.shape[:2]
    dh, dw = int(h * (1 - frac) / 2), int(w * (1 - frac) / 2)
    return img[dh : h - dh, dw : w - dw]


def sinais(bgr: np.ndarray) -> dict[str, float]:
    """As 12 features V0 de uma foto BGR uint8.

    lap_var: variancia do Laplaciano (nitidez; baixa = fora de foco).
    frac_estourados: V > 250 e S < 30 no HSV (reflexo). frac_escuros: V < 40.
    entropia_hist: entropia do histograma de cinza (tampa suja/nevoa achata o histograma).
    bordas_centro: densidade de bordas Canny no centro (enquadramento).
    """
    cinza = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    lap = cv2.Laplacian(cinza, cv2.CV_64F)
    gx = cv2.Sobel(cinza, cv2.CV_64F, 1, 0)
    gy = cv2.Sobel(cinza, cv2.CV_64F, 0, 1)
    hist = np.bincount(cinza.ravel(), minlength=256).astype(np.float64)
    p = hist / hist.sum()
    p = p[p > 0]
    p01, p99 = np.percentile(cinza, [1, 99])
    return {
        "lap_var": float(lap.var()),
        "lap_var_centro": float(_centro(lap).var()),
        "tenengrad": float(np.mean(gx * gx + gy * gy)),
        "brilho_media": float(cinza.mean()),
        "contraste": float(cinza.std()),
        "saturacao_media": float(s.mean()),
        "frac_estourados": float(((v > 250) & (s < 30)).mean()),
        "frac_escuros": float((v < 40).mean()),
        "entropia_hist": float(-(p * np.log2(p)).sum()),
        "bordas_centro": float(cv2.Canny(_centro(cinza), 100, 200).mean() / 255.0),
        "p01": float(p01),
        "p99": float(p99),
    }


# ---------------------------------------------------------------------------
# Scores de "ruim" em [0, 1], um por classe de qualidade (plano 04, secao 1.1)
# ---------------------------------------------------------------------------

# Rampa de cada sinal: valor `ok` vira score 0, valor `lixo` vira 1, linear entre
# os dois (em log quando o sinal varia em ordens de grandeza). Fazem parte do
# detector `heuristica@v1`: mudar qualquer numero aqui e um detector novo
# (`heuristica@v2`), que exige reprocessar as imagens. O ponto de corte que
# decide fica em `inference.politica`, e esse muda sem reabrir imagem.
#
# `ok` = mediana das 12.340 fotos da base, salvo onde dito. Medido em
# ml/reports/heuristicas.md.
ESCALAS: dict[str, tuple[float, float, bool]] = {
    # (ok, lixo, log)
    "tenengrad": (3668.0, 50.0, True),  # 50 ~ percentil 0,4: quadro sem detalhe
    "frac_escuros": (0.5, 1.0, False),  # ate metade escura ainda ha foto noturna legivel
    "frac_estourados": (0.0, 0.5, False),  # metade do quadro estourado
    "contraste": (53.0, 0.0, False),  # 0 = quadro de cor unica
    "bordas_centro": (0.057, 0.0, False),
}


def _rampa(valor: float, ok: float, lixo: float, log: bool) -> float:
    if log:
        valor, ok, lixo = np.log1p(valor), np.log1p(ok), np.log1p(lixo)
    return float(np.clip((valor - ok) / (lixo - ok), 0.0, 1.0))


def scores(s: dict[str, float]) -> dict[str, float]:
    """Converte os sinais brutos nos 5 scores de classe (`politica.CLASSES`).

    Toda classe passa pela nitidez (minimo com o score de tenengrad): foto com
    detalhe nitido nao e "lixo obvio" mesmo escura, estourada ou de centro liso.
    Nas fotos rotuladas, quadro escuro com medidor nitido (armario fechado, flash
    fraco) e fundo branco estourado em volta do medidor foram rotulados legiveis.

    fora_de_foco: tenengrad (energia do gradiente). O plano previa a variancia do
        Laplaciano; nas fotos rotuladas a cauda do tenengrad separa melhor (ver report).
    glare: fracao de pixels estourados E nitidez baixa.
    display_apagado: fracao de pixels escuros E nitidez baixa.
    tampa_suja: contraste baixo E nitidez baixa (o minimo dos dois).
    enquadramento_incorreto: centro sem bordas E nitidez baixa. Centro liso em foto
        nitida e comum (display grande, tampa lisa) e foi rotulado legivel.
    """
    r = {nome: _rampa(s[nome], *ESCALAS[nome]) for nome in ESCALAS}
    return {
        "fora_de_foco": r["tenengrad"],
        "glare": min(r["frac_estourados"], r["tenengrad"]),
        "tampa_suja": min(r["contraste"], r["tenengrad"]),
        "enquadramento_incorreto": min(r["bordas_centro"], r["tenengrad"]),
        "display_apagado": min(r["frac_escuros"], r["tenengrad"]),
    }


def avaliar(bgr: np.ndarray) -> dict:
    """Scores das 5 classes no nivel de cima e os sinais brutos em `sinais`.

    Formato gravado em `resultados_inferencia.saida`. A politica so le as chaves
    de `politica.CLASSES`; `sinais` fica para auditoria e recalibracao.
    """
    brutos = sinais(bgr)
    return {**scores(brutos), "sinais": brutos}

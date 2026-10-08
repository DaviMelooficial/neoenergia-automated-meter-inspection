"""Degradacao sintetica: fabrica exemplos de defeito a partir de fotos limpas (rotulo fraco).

Cada funcao recebe RGB uint8 e um `np.random.Generator` e devolve RGB uint8 do mesmo
tamanho. Mesma seed -> mesma saida (testado em ml/tests/test_degradacao.py).

A regiao do display nao e conhecida (nao ha caixa rotulada); reflexo e display apagado
usam a regiao central da foto como aproximacao, o que e uma limitacao declarada do K2.
"""

from __future__ import annotations

import cv2
import numpy as np

from mlinsp.dados import COM_MEDIDOR, DEFEITOS


def _regiao_central(h: int, w: int, rng: np.random.Generator) -> tuple[int, int, int, int]:
    ch, cw = int(h * rng.uniform(0.15, 0.30)), int(w * rng.uniform(0.25, 0.45))
    cy = int(h * rng.uniform(0.35, 0.60))
    cx = int(w * rng.uniform(0.35, 0.65))
    return max(cy - ch // 2, 0), min(cy + ch // 2, h), max(cx - cw // 2, 0), min(cx + cw // 2, w)


def borrao(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Fora de foco: gaussiano (sigma 1,2-4,0) ou tremido (kernel linear 5-15 px)."""
    if rng.random() < 0.6:
        s = float(rng.uniform(1.2, 4.0))
        return cv2.GaussianBlur(img, (0, 0), sigmaX=s, sigmaY=s)
    k = int(rng.integers(5, 16))
    kernel = np.zeros((k, k), np.float32)
    kernel[k // 2, :] = 1.0
    rot = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), float(rng.uniform(0, 180)), 1.0)
    kernel = cv2.warpAffine(kernel, rot, (k, k))
    kernel /= max(kernel.sum(), 1e-6)
    return cv2.filter2D(img, -1, kernel)


def downscale(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Fora de foco por perda de resolucao: reduz 2-4x e volta ao tamanho original."""
    h, w = img.shape[:2]
    f = float(rng.uniform(0.25, 0.5))
    peq = cv2.resize(img, (max(int(w * f), 1), max(int(h * f), 1)), interpolation=cv2.INTER_AREA)
    return cv2.resize(peq, (w, h), interpolation=cv2.INTER_LINEAR)


def reflexo(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Mancha radial branca aditiva (raio 15-40% da menor dimensao, alfa 0,35-0,85)."""
    h, w = img.shape[:2]
    y0, y1, x0, x1 = _regiao_central(h, w, rng)
    cy, cx = rng.integers(y0, max(y1, y0 + 1)), rng.integers(x0, max(x1, x0 + 1))
    raio = float(rng.uniform(0.15, 0.40)) * min(h, w)
    alfa = float(rng.uniform(0.35, 0.85))
    yy, xx = np.mgrid[0:h, 0:w]
    mascara = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * (raio / 2) ** 2))
    out = img.astype(np.float32) + alfa * 255.0 * mascara[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def nevoa(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Tampa suja/embacada: contraste reduzido (a 0,35-0,65), veu claro e mancha de baixa
    frequencia."""
    h, w = img.shape[:2]
    a = float(rng.uniform(0.35, 0.65))
    veu = float(rng.uniform(60, 140))
    ruido = rng.normal(0, 1, (max(h // 32, 2), max(w // 32, 2))).astype(np.float32)
    ruido = cv2.resize(ruido, (w, h), interpolation=cv2.INTER_CUBIC)
    ruido = cv2.GaussianBlur(ruido, (0, 0), 8) * float(rng.uniform(10, 30))
    out = img.astype(np.float32) * a + veu * (1 - a) + ruido[..., None]
    out = cv2.GaussianBlur(out, (0, 0), float(rng.uniform(0.8, 1.6)))
    return np.clip(out, 0, 255).astype(np.uint8)


def recorte_display(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Enquadramento: recorta 30-60% deslocado para uma borda (o centro, onde costuma
    estar o display, sai do quadro) e reescala ao tamanho original."""
    h, w = img.shape[:2]
    f = float(rng.uniform(0.30, 0.60))
    ch, cw = int(h * f), int(w * f)
    borda = int(rng.integers(0, 4))
    if borda == 0:
        y, x = 0, int(rng.integers(0, w - cw + 1))
    elif borda == 1:
        y, x = h - ch, int(rng.integers(0, w - cw + 1))
    elif borda == 2:
        y, x = int(rng.integers(0, h - ch + 1)), 0
    else:
        y, x = int(rng.integers(0, h - ch + 1)), w - cw
    return cv2.resize(img[y:y + ch, x:x + cw], (w, h), interpolation=cv2.INTER_LINEAR)


def escurecer_display(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Display apagado: gama 1,8-3,0 numa regiao central, suavizada nas bordas."""
    h, w = img.shape[:2]
    y0, y1, x0, x1 = _regiao_central(h, w, rng)
    gama = float(rng.uniform(1.8, 3.0))
    mascara = np.zeros((h, w), np.float32)
    mascara[y0:y1, x0:x1] = 1.0
    mascara = cv2.GaussianBlur(mascara, (0, 0), 4)[..., None]
    base = img.astype(np.float32) / 255.0
    escuro = np.power(base, gama) * 0.6
    return np.clip((base * (1 - mascara) + escuro * mascara) * 255, 0, 255).astype(np.uint8)


def recomprimir_jpeg(img: np.ndarray, rng: np.random.Generator, qmin: int = 60,
                     qmax: int = 95) -> np.ndarray:
    """Recompressao JPEG aplicada a TODAS as variantes (limpas inclusive), para a rede nao
    aprender 'artefato de JPEG = defeito'."""
    q = int(rng.integers(qmin, qmax + 1))
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        raise RuntimeError("falha ao recomprimir JPEG")
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


# defeito -> transformacoes que o produzem (uma e sorteada)
TRANSFORMACOES = {
    "reflexo": [reflexo],
    "fora_de_foco": [borrao, downscale],
    "tampa_suja": [nevoa],
    "enquadramento": [recorte_display],
    "display_apagado": [escurecer_display],
}
assert list(TRANSFORMACOES) == DEFEITOS


def degradar(img: np.ndarray, defeitos: list[str], seed: int) -> np.ndarray:
    """Aplica os defeitos pedidos (na ordem de DEFEITOS) e a recompressao JPEG."""
    rng = np.random.default_rng(seed)
    out = img
    for d in DEFEITOS:
        if d in defeitos:
            fns = TRANSFORMACOES[d]
            out = fns[int(rng.integers(0, len(fns)))](out, rng)
    return recomprimir_jpeg(out, rng)


def sortear_defeitos(rng: np.random.Generator, max_defeitos: int = 2) -> list[str]:
    k = int(rng.integers(1, max_defeitos + 1))
    return sorted(rng.choice(DEFEITOS, size=k, replace=False).tolist(), key=DEFEITOS.index)


def rotulo_apos_degradacao(rotulo_real: dict[str, int], aplicados: list[str]) -> dict[str, int]:
    """Regra do K2 para foto real que ja tem defeito e recebe degradacao.

    O rotulo final e o OU logico: defeito real X continua X=1 (borrar uma foto ja borrada
    nao a torna 'menos' borrada) e todo defeito aplicado vira 1. Legivel so pode cair:
    qualquer degradacao aplicada leva legivel a 0.
    """
    out = {d: int(bool(rotulo_real.get(d, 0)) or d in aplicados) for d in DEFEITOS}
    out["legivel"] = int(bool(rotulo_real.get("legivel", 1)) and not aplicados)
    return out


def eh_fonte_limpa(rotulo: dict) -> bool:
    """Foto que pode ser origem de exemplo sintetico: medidor visivel, nenhum defeito
    e legivel. Foto com defeito real nunca e 'boa' de origem (ver rotulo_apos_degradacao)."""
    if rotulo.get("cena") not in COM_MEDIDOR or rotulo.get("legivel") != 1:
        return False
    return all(rotulo.get(d) == 0 for d in DEFEITOS)


def gerar_sintetico(img: np.ndarray, seed: int, n_degradadas: int = 2,
                    max_defeitos: int = 2) -> list[tuple[np.ndarray, dict[str, int]]]:
    """Uma foto limpa -> 1 variante limpa (so JPEG) + n_degradadas com 1-2 defeitos."""
    rng = np.random.default_rng(seed)
    base = {d: 0 for d in DEFEITOS} | {"legivel": 1}
    saida = [(degradar(img, [], int(rng.integers(2**31))), rotulo_apos_degradacao(base, []))]
    for _ in range(n_degradadas):
        aplicados = sortear_defeitos(rng, max_defeitos)
        saida.append((degradar(img, aplicados, int(rng.integers(2**31))),
                      rotulo_apos_degradacao(base, aplicados)))
    return saida

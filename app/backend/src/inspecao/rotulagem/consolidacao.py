"""Consolidacao por maioria e kappa de Cohen por par de rotuladores.

Porte de `rotulador/consolidar.py` (Lab 2) sem depender dele: as mesmas regras,
para que o kappa calculado aqui sobre a rodada `lab02` bata com
`lab-02/resultados/resumo.json`. Funcoes puras, sem banco: quem chama monta
`por_foto` (foto -> rotulador -> {classe: valor}).

Valores: `cena` e texto; defeitos e `legivel` sao 0 ou 1. Empate na maioria vira
`None` (NA): a foto fica fora do treino daquela cabeca.
"""

from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from inspecao.enums import Cena, Defeito

DEFEITOS: tuple[str, ...] = tuple(d.value for d in Defeito)
CLASSES: tuple[str, ...] = ("cena", *DEFEITOS, "legivel")
LIMIAR_KAPPA = 0.6

Valor = str | int
Voto = Mapping[str, Valor]
PorFoto = Mapping[Hashable, Mapping[str, Voto]]


def kappa_cohen(a: Sequence[Valor], b: Sequence[Valor]) -> float | None:
    """Kappa de Cohen entre duas listas alinhadas (mesmas fotos, mesma ordem).

    po = fracao de fotos com o mesmo rotulo; pe = soma, por categoria, de
    p_a(c) * p_b(c); kappa = (po - pe) / (1 - pe). Indefinido (None) sem fotos ou
    quando pe == 1 (os dois usaram uma unica e mesma categoria em tudo).
    """
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def maioria(valores: Sequence[Valor]) -> tuple[Valor | None, bool]:
    """Rotulo mais votado; empate entre os mais votados -> (None, True)."""
    contagem = Counter(valores).most_common()
    if len(contagem) > 1 and contagem[0][1] == contagem[1][1]:
        return None, True
    return contagem[0][0], False


def aplicar_regra(saida: dict[str, Valor | None], empates: set[str]) -> None:
    """Mesma regra do app: sem medidor visivel, defeitos e legivel sao 0 (e nao empatam)."""
    if saida["cena"] == Cena.NAO_MEDIDOR:
        for d in DEFEITOS:
            saida[d] = 0
            empates.discard(d)
    if saida["cena"] in (Cena.NAO_MEDIDOR, Cena.OCORRENCIA):
        saida["legivel"] = 0
        empates.discard("legivel")


@dataclass(frozen=True, slots=True)
class Consolidado:
    foto: Hashable
    n_rotuladores: int
    valores: dict[str, Valor | None]
    empates: frozenset[str]

    @property
    def empate(self) -> bool:
        return bool(self.empates)


def consolidar(por_foto: PorFoto) -> list[Consolidado]:
    saida = []
    for foto in sorted(por_foto):
        votos = por_foto[foto]
        valores: dict[str, Valor | None] = {}
        empates: set[str] = set()
        for c in CLASSES:
            valor, empate = maioria([v[c] for v in votos.values()])
            valores[c] = valor
            if empate:
                empates.add(c)
        aplicar_regra(valores, empates)
        saida.append(Consolidado(foto, len(votos), valores, frozenset(empates)))
    return saida


@dataclass(frozen=True, slots=True)
class KappaPar:
    a: str
    b: str
    n: int
    kappa: dict[str, float | None]


@dataclass(frozen=True, slots=True)
class KappaClasse:
    kappa: float | None
    n: int  # pares-foto que entraram na media

    @property
    def passa(self) -> bool:
        return self.kappa is not None and self.kappa >= LIMIAR_KAPPA


def kappa_por_par(por_foto: PorFoto) -> list[KappaPar]:
    rotuladores = sorted({r for votos in por_foto.values() for r in votos})
    pares = []
    for a, b in combinations(rotuladores, 2):
        comuns = sorted(f for f, v in por_foto.items() if a in v and b in v)
        if not comuns:
            continue
        kappas = {
            c: kappa_cohen([por_foto[f][a][c] for f in comuns], [por_foto[f][b][c] for f in comuns])
            for c in CLASSES
        }
        pares.append(KappaPar(a, b, len(comuns), kappas))
    return pares


def kappa_medio(pares: Sequence[KappaPar]) -> dict[str, KappaClasse]:
    """Media dos pares ponderada pelo n de fotos em comum; par com kappa indefinido sai."""
    medio = {}
    for c in CLASSES:
        validos = [(p.n, p.kappa[c]) for p in pares if p.kappa[c] is not None]
        peso = sum(n for n, _ in validos)
        medio[c] = KappaClasse(sum(n * k for n, k in validos) / peso if peso else None, peso)
    return medio

"""Lote sintetico compartilhado pelos testes de integracao.

Cobre os caminhos que os 4 lotes reais nao exercitam. Nao e arquivo de teste:
nao comeca com `test_`.
"""

import csv
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from inspecao.ingest.batch import BatchFolderSource, ResultadoIngestao, ingerir
from inspecao.models import Lote, NotaOcorrencia
from inspecao.storage.local import LocalFileStorage

LOTE = "PSP_EXTRATLEITIMPL_010199_0000"  # data impossivel: nunca colide com lote real
PREFIXO = f"{LOTE}_"
CABECALHO = [
    "Numero do medidor",
    "Posicao do medidor lida",
    "Nota de Leitura Atual",
    "Foto do medidor",
]


def foto(id_externo: int, seq: int = 0) -> str:
    return f"{PREFIXO}{id_externo:020d}_{seq:03d}.jpg"


# linha_csv comeca em 2 (a 1 e o cabecalho). Uma linha por cenario.
LINHAS = [
    # 2: normal, sem nota, com foto -> segue para triagem, sem quarentena
    ["1001", "123", "NA", foto(1)],
    # 3: nota exige foto e nao tem -> bloqueante + sem_foto
    ["1002", "456", "Z901", "NA"],
    # 4: nota nao exige e tem foto -> informativa, segue
    ["1003", "789", "Z902", foto(3)],
    # 5: codigo desconhecido, sem foto -> informativa + sem_foto
    ["1004", "NA", "Z999", "NA"],
    # 6: tres medidores, com foto -> medidor_multiplo, segue
    ["2001/2002/F2003", "10", "NA", foto(6)],
    # 7: CSV aponta foto que nao existe em disco -> bloqueante + sem_foto
    ["1007", "11", "Z901", foto(7)],
    # 8: posicao nao numerica -> posicao_invalida
    ["1008", "12ab", "NA", foto(8)],
    # 9: sem foto e sem nota -> sem_foto, sem quarentena
    ["1009", "13", "NA", "NA"],
    # 10: nota que pressupoe leitura (e nao exige foto), posicao vazia, com foto
    #     -> posicao_ausente + foto_sem_exigencia
    ["1010", "NA", "T111", foto(10)],
    # 11: aponta o MESMO arquivo da linha 2 -> a foto fica com a linha 2; esta vira
    #     foto_referenciada_ausente (bloqueante) + sem_foto
    ["1011", "14", "NA", foto(1)],
]
FOTOS_EM_DISCO = [foto(1), foto(3), foto(6), foto(8), foto(10), foto(999, 2)]  # 999 = orfa
LINHAS_COM_FOTO = [2, 4, 6, 8, 10]
LINHAS_SEM_FOTO = [3, 5, 7, 9, 11]


def jpeg_bytes() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((480, 360, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


def criar_staging(raiz: Path) -> Path:
    pasta = raiz / LOTE
    pasta.mkdir(parents=True)
    with (pasta / "BaseExtracao_01011999_Dia.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        w.writerow(CABECALHO)
        w.writerows(LINHAS)
    dados = jpeg_bytes()
    for nome in FOTOS_EM_DISCO:
        (pasta / nome).write_bytes(dados)
    return raiz


def garantir_notas(sessao: Session) -> None:
    """Notas sinteticas, na transacao do teste. Nao dependem da planilha do cliente."""
    for codigo, descricao, exige in [
        ("Z901", "exige foto", True),
        ("Z902", "nao exige foto", False),
        ("T111", "tampa danificada", False),
    ]:
        nota = sessao.get(NotaOcorrencia, codigo)
        if nota is None:
            sessao.add(NotaOcorrencia(codigo=codigo, descricao=descricao, exige_foto=exige))
        else:
            nota.exige_foto = exige
    sessao.flush()


def ingerir_sintetico(
    sessao: Session, staging: Path, storage_raiz: Path
) -> tuple[ResultadoIngestao, Lote, LocalFileStorage]:
    garantir_notas(sessao)
    storage = LocalFileStorage(raiz=storage_raiz)
    resultado = ingerir(sessao, BatchFolderSource(staging), LOTE, storage)
    lote = sessao.scalar(select(Lote).where(Lote.nome == LOTE))
    return resultado, lote, storage

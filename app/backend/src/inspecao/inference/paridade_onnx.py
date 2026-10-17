"""Paridade de ponta a ponta do modelo do M7: DetectorOnnx (backend) contra o torch (ml).

    python -m inspecao.inference.paridade_onnx \\
        --referencia ../../ml/artifacts/m7/<rodada>/p_ilegivel_base.csv \\
        --base "../../Base cliente" --saida ../../ml/reports/<rodada>/paridade_backend.json

A referencia e o `p_ilegivel` que `mlinsp.evaluate` calculou no torch (MPS) para cada
foto da base. Aqui cada foto passa pelo caminho real do worker: bytes do JPEG,
`decodificar`, `DetectorOnnx.avaliar` (pre-processamento em numpy + onnxruntime em CPU).
Le arquivos e o modelo; nao toca em banco.
"""

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from inspecao.config import get_settings
from inspecao.inference.detector import decodificar
from inspecao.inference.politica import politica_para
from inspecao.inference.qualidade_onnx import DetectorOnnx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="paridade DetectorOnnx x torch")
    ap.add_argument("--referencia", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help='pasta "Base cliente"')
    ap.add_argument("--saida", type=Path, required=True)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args(argv)

    s = get_settings()
    det = DetectorOnnx(s.caminho_absoluto(s.modelo_qualidade_path))
    politica = politica_para(det.nome)
    with open(args.referencia, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))

    def um(linha: dict) -> tuple[float, float]:
        dados = (args.base / linha["lote"] / linha["arquivo"]).read_bytes()
        t0 = time.perf_counter()
        p = det.avaliar(decodificar(dados))["ilegivel"]
        return p, (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    with ThreadPoolExecutor(args.threads) as ex:
        resultados = list(ex.map(um, linhas))
    total = time.perf_counter() - t0
    backend = np.array([r[0] for r in resultados])
    ms = np.array([r[1] for r in resultados])
    torch_ = np.array([float(linha["p_ilegivel"]) for linha in linhas])
    dif = np.abs(backend - torch_)
    limiar = politica.limiar_rejeita
    rej_b, rej_t = backend >= limiar, torch_ >= limiar
    relatorio = {
        "modelo": det.nome,
        "onnx_sha256": det.contrato["onnx_sha256"],
        "politica": politica.versao,
        "fotos": len(linhas),
        "max_dif_abs_p_ilegivel": float(dif.max()),
        "p99_dif_abs": float(np.percentile(dif, 99)),
        "fotos_acima_1e-4": int((dif >= 1e-4).sum()),
        "rejeitadas_backend": int(rej_b.sum()),
        "rejeitadas_torch": int(rej_t.sum()),
        "decisoes_diferentes": int((rej_b != rej_t).sum()),
        "latencia_ms_por_foto": {
            "p50": float(np.percentile(ms, 50)),
            "p95": float(np.percentile(ms, 95)),
            "media": float(ms.mean()),
            "nota": f"onnxruntime CPU 1 thread por chamada, {args.threads} chamadas em paralelo",
        },
        "segundos_total": round(total, 1),
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(relatorio, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(relatorio, indent=1, ensure_ascii=False))
    return 0 if relatorio["decisoes_diferentes"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

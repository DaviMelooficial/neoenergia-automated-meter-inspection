#!/usr/bin/env bash
# Executa o notebook do Lab 2 do zero (equivale a "Reiniciar e executar tudo").
#
#   bash lab-02/executar.sh
#
# 1. Se houver rotulos em rotulador/rotulos/<pessoa>/rotulos.csv, roda o consolidar.py
#    (gera rotulador/saida/rotulos_consolidados.csv, todos_os_rotulos.csv e kappa.md).
# 2. Executa lab-02/lab02.ipynb no lugar, com as saidas gravadas no proprio .ipynb.
#
# Variaveis opcionais: LAB02_BASE (fotos), LAB02_ROTULOS_DIR (saida do consolidar),
# LAB02_CACHE (features), LAB02_RESULTADOS (resumo.csv/json).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

# Quem clona o repo não tem rotulador/rotulos/ (ignorado pelo git): remonta uma pasta por
# rotulador a partir do lab-02/rotulos/rotulos.csv entregue, para o consolidar.py rodar igual.
if [ -z "${LAB02_ROTULOS_DIR:-}" ] && ! ls "$RAIZ"/rotulador/rotulos/*/rotulos.csv >/dev/null 2>&1 \
   && [ -f "$RAIZ/lab-02/rotulos/rotulos.csv" ]; then
  echo "remontando rotulador/rotulos/ a partir de lab-02/rotulos/rotulos.csv ..."
  python3 - "$RAIZ" <<'PY'
import csv, os, sys
raiz = sys.argv[1]
with open(os.path.join(raiz, "lab-02/rotulos/rotulos.csv"), encoding="utf-8") as f:
    leitor = csv.DictReader(f)
    por_pessoa = {}
    for linha in leitor:
        por_pessoa.setdefault(linha["rotulador"], []).append(linha)
    colunas = leitor.fieldnames
for pessoa, linhas in por_pessoa.items():
    pasta = os.path.join(raiz, "rotulador/rotulos", pessoa)
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "rotulos.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)
PY
fi

if [ -z "${LAB02_ROTULOS_DIR:-}" ] && ls "$RAIZ"/rotulador/rotulos/*/rotulos.csv >/dev/null 2>&1; then
  echo "consolidando rotulos de rotulador/rotulos/ ..."
  python3 "$RAIZ/rotulador/consolidar.py" > /dev/null
fi

inicio=$(date +%s)
uv run --directory "$RAIZ/ml" jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.timeout=1800 "$RAIZ/lab-02/lab02.ipynb"
echo "notebook executado em $(( $(date +%s) - inicio )) s"

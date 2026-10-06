#!/bin/bash
# Duplo clique para abrir o rotulador no navegador. Feche esta janela para sair.
cd "$(dirname "$0")" || exit 1
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
  python3 rotulador.py
elif command -v uv >/dev/null 2>&1; then
  uv run rotulador.py
elif [ -x "$HOME/.local/bin/uv" ]; then
  "$HOME/.local/bin/uv" run rotulador.py
else
  echo "Nao encontrei o Python 3."
  echo "Instale em https://www.python.org/downloads/ e de duplo clique de novo neste arquivo."
fi
echo
read -r -p "Pressione Enter para fechar esta janela..."

@echo off
rem Duplo clique para abrir o rotulador no navegador. Feche esta janela para sair.
rem Usa o primeiro Python 3.8+ que achar; senao tenta o uv (que baixa um Python sozinho).
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && (py -3 rotulador.py & goto fim)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && (python rotulador.py & goto fim)
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && (python3 rotulador.py & goto fim)
uv --version >nul 2>&1 && (uv run rotulador.py & goto fim)
if exist "%USERPROFILE%\.local\bin\uv.exe" ("%USERPROFILE%\.local\bin\uv.exe" run rotulador.py & goto fim)
echo Nao encontrei o Python 3.8 ou mais novo.
echo Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH" e abra este arquivo de novo.
:fim
echo.
pause

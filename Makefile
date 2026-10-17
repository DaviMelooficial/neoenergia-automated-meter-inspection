.DEFAULT_GOAL := help
.PHONY: help setup env db-up db-down migrate revision seed-staging ingest ingest-todos triagem-todos recalcular-todos relatorio-heuristicas fila-resumo tipos-front \
        api-dev worker-dev front-dev test lint up down logs reset health lab02 \
        m7-treinar m7-calibrar m7-avaliar m7-exportar m7-paridade

help:  ## lista os alvos disponiveis
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

env:  ## cria .env a partir do exemplo, se ainda nao existir
	@test -f .env || (cp .env.example .env && echo "criado .env a partir de .env.example")

setup: env  ## instala toda a toolchain e as dependencias
	uv python install 3.12
	cd app/backend && uv sync
	cd ml && uv sync
	cd app/frontend && npm install

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

db-up:  ## sobe apenas o postgres
	docker compose up -d postgres
	@printf "aguardando o postgres"
	@until docker compose exec -T postgres pg_isready -q 2>/dev/null; do printf "."; sleep 1; done
	@echo " pronto"

db-down:  ## para o postgres
	docker compose stop postgres

migrate:  ## aplica as migracoes
	cd app/backend && uv run alembic upgrade head

revision:  ## cria migracao (uso: make revision M="descricao")
	@test -n "$(M)" || (echo 'uso: make revision M="descricao"'; exit 1)
	cd app/backend && uv run alembic revision --autogenerate -m "$(M)"

# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------

seed-staging:  ## copia um lote de "Base cliente" para data/staging (uso: make seed-staging LOTE=...)
	@test -n "$(LOTE)" || (echo 'uso: make seed-staging LOTE=PSP_EXTRATLEITIMPL_200526_0352'; exit 1)
	@mkdir -p data/staging
	cp -R "Base cliente/$(LOTE)" data/staging/
	@echo "lote $(LOTE) pronto em data/staging"

ingest:  ## ingere um lote de data/staging (uso: make ingest LOTE=...)
	@test -n "$(LOTE)" || (echo 'uso: make ingest LOTE=PSP_EXTRATLEITIMPL_200526_0352'; exit 1)
	cd app/backend && uv run python -m inspecao.ingest.batch --lote $(LOTE)

ingest-todos:  ## ingere todos os lotes de data/staging (use REPROCESSAR=1 para substituir)
	cd app/backend && uv run python -m inspecao.ingest.batch --todos $(if $(REPROCESSAR),--reprocessar,)

# ---------------------------------------------------------------------------
# Desenvolvimento (processos no host, com hot reload)
# ---------------------------------------------------------------------------

api-dev:  ## sobe a api no host com reload
	cd app/backend && uv run uvicorn inspecao.main:app --reload --port 8000

worker-dev:  ## sobe o worker no host
	cd app/backend && uv run python -m inspecao.jobs.worker

triagem-todos:  ## enfileira triagem: "recebida" e as sem resultado do detector atual (volta a detector ja usado: recalcular-todos)
	cd app/backend && uv run python -m inspecao.jobs.cli reprocessar --todos

recalcular-todos:  ## reaplica a politica aos resultados gravados, sem reabrir imagem (POLITICA=; padrao: a do detector configurado)
	cd app/backend && uv run python -m inspecao.jobs.cli recalcular --todos $(if $(POLITICA),--politica $(POLITICA),)

relatorio-heuristicas:  ## exporta os scores do banco e gera ml/reports/heuristicas_* (LIMIAR=limiar_rejeita da v1)
	@test -n "$(LIMIAR)" || (echo 'uso: make relatorio-heuristicas LIMIAR=0.85'; exit 1)
	@mkdir -p ml/artifacts
	docker compose exec -T postgres psql -U $${POSTGRES_USER:-inspecao} -d $${POSTGRES_DB:-inspecao} \
	  -v ON_ERROR_STOP=1 -q < ml/reports/heuristicas_exportacao.sql > ml/artifacts/heuristicas_exportacao.csv
	cd ml && uv run python -m mlinsp.relatorio_heuristicas --exportacao artifacts/heuristicas_exportacao.csv \
	  --base "../Base cliente" --limiar $(LIMIAR)

fila-resumo:  ## contagem de jobs por tipo e status
	cd app/backend && uv run python -m inspecao.jobs.cli resumo

front-dev:  ## sobe o vite dev server
	cd app/frontend && npm run dev

tipos-front:  ## regenera openapi.json e os tipos TypeScript do frontend a partir da API
	cd app/backend && uv run python -c "import json; from inspecao.main import app; json.dump(app.openapi(), open('../frontend/openapi.json','w'), indent=1, ensure_ascii=False)"
	cd app/frontend && npm run tipos

# ---------------------------------------------------------------------------
# Qualidade
# ---------------------------------------------------------------------------

test:  ## roda os testes de backend e frontend
	cd app/backend && uv run pytest -q
	cd app/frontend && npm run test -- --run

lint:  ## roda os linters
	cd app/backend && uv run ruff check src tests
	cd ml && uv run ruff check src tests
	cd app/frontend && npm run lint

# ---------------------------------------------------------------------------
# Lab 2 (Deep Learning)
# ---------------------------------------------------------------------------

lab02:  ## consolida os rotulos e executa lab-02/lab02.ipynb do zero (saidas no .ipynb)
	bash lab-02/executar.sh

# ---------------------------------------------------------------------------
# M7 (modelo de qualidade). Exige ml/artifacts/heuristicas_exportacao.csv
# (make relatorio-heuristicas). RODADA = pasta criada em ml/reports/ pelo m7-treinar.
# ---------------------------------------------------------------------------

M7_ENTRADAS = --base "../Base cliente" --exportacao artifacts/heuristicas_exportacao.csv

m7-treinar:  ## treina o modelo de qualidade (2 estagios, 3 sementes + repeticao); cria a RODADA
	cd ml && uv run python -m mlinsp.train_quality $(M7_ENTRADAS)

m7-calibrar:  ## temperatura e limiar de rejeicao na validacao (uso: make m7-calibrar RODADA=...)
	@test -n "$(RODADA)" || (echo 'uso: make m7-calibrar RODADA=AAAAMMDD-HHMMSS'; exit 1)
	cd ml && uv run python -m mlinsp.calibrate --rodada $(RODADA)

m7-avaliar:  ## metricas no teste contra a heuristica e criterio de entrada (RODADA=...)
	@test -n "$(RODADA)" || (echo 'uso: make m7-avaliar RODADA=AAAAMMDD-HHMMSS'; exit 1)
	cd ml && uv run python -m mlinsp.evaluate --rodada $(RODADA) $(M7_ENTRADAS)

m7-exportar:  ## gera ml/artifacts/qualidade.onnx + qualidade.json com paridade < 1e-4 (RODADA=...)
	@test -n "$(RODADA)" || (echo 'uso: make m7-exportar RODADA=AAAAMMDD-HHMMSS'; exit 1)
	cd ml && uv run python -m mlinsp.export_onnx --rodada $(RODADA) --base "../Base cliente"

m7-paridade:  ## DetectorOnnx do backend contra o torch na base inteira, sem banco (RODADA=...)
	@test -n "$(RODADA)" || (echo 'uso: make m7-paridade RODADA=AAAAMMDD-HHMMSS'; exit 1)
	cd app/backend && uv run python -m inspecao.inference.paridade_onnx \
	  --referencia ../../ml/artifacts/m7/$(RODADA)/p_ilegivel_base.csv \
	  --base "../../Base cliente" --saida ../../ml/reports/$(RODADA)/paridade_backend.json

# ---------------------------------------------------------------------------
# Stack completa
# ---------------------------------------------------------------------------

up: env  ## sobe a stack completa em container
	docker compose up -d --build

down:  ## derruba a stack
	docker compose down

logs:  ## acompanha os logs
	docker compose logs -f

health:  ## consulta o /health da api
	@curl -fsS http://localhost:8000/api/health | python3 -m json.tool

reset:  ## APAGA banco, storage e staging. Recomeca do zero
	docker compose down -v
	rm -rf data/storage data/staging
	mkdir -p data/storage data/staging
	@echo "estado local zerado"

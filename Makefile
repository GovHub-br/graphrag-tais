RAG_SERVICES := qdrant neo4j minio postgres-tunnel postgres-mcp hayhooks
PYTHON ?= python3

# Persona 1 (modo Explorar, ADR-0014)
EXPLORE_DATASET ?= eval/datasets/explore/mcid-explore-v0.1.0.json
EXPLORE_RUN ?= eval/reports/explore-run.json
EXPLORE_REPORT ?= eval/reports/explore-report.json
EXPLORE_MARKDOWN ?= eval/reports/explore-report.md
EXPLORE_POLICY ?= eval/config/explore-policy-v0.1.0.json

.PHONY: up eval-quick eval-collect eval-live eval-score eval-full eval-answer \
	explore-quick eval-explore explore-score explore-gate docs-build docs-serve docs-image
up:
	docker compose up -d --build --no-deps $(RAG_SERVICES)

eval-quick:
	$(MAKE) -C eval quick \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)"

eval-collect:
	EVAL_DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-business-v0.1.0.json)" \
	EVAL_TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
	EVAL_CHANNEL="$(or $(EVAL_CHANNEL),hybrid)" \
	./scripts/run_retrieval_experiment_docker.sh

eval-live: eval-collect
	$(MAKE) eval-score \
		EVAL_TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		EVAL_DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-business-v0.1.0.json)"

eval-score:
	$(MAKE) -C eval score \
		TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)"

# Qualidade da RESPOSTA (item 0.3). Diferente dos demais, ESTE CHAMA O LLM —
# roda no container, contra a config vigente. Fidelidade, JOIN autorizado e grão
# são determinísticos: parse do SQL contra o subgrafo que a execução recuperou.
eval-answer:
	docker exec hayhooks rm -rf /app/eval /app/tests
	docker cp eval hayhooks:/app/eval
	docker cp tests hayhooks:/app/tests
	docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \
		python -m unittest tests.test_answer_eval
	docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \
		python -m eval.answer.live \
		--dataset "/app/$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-business-v0.1.0.json)" \
		--output /tmp/answer-run.json
	docker cp hayhooks:/tmp/answer-run.json \
		"$(or $(EVAL_ANSWER_OUT),eval/reports/answer-run.json)"

# --------------------------------------------------------------------------- #
# Persona 1 — modo Explorar (ADR-0014)
# --------------------------------------------------------------------------- #
# Offline: valida o contrato do gold e exercita o motor de métricas em fixtures.
# Não toca em serviço nenhum e não chama LLM — roda no host, em segundos.
explore-quick:
	$(PYTHON) -m eval.explore.cli validate --dataset "$(EXPLORE_DATASET)"
	$(PYTHON) -m unittest tests.test_explore_eval
	$(PYTHON) -m eval.explore.cli score \
		--dataset eval/fixtures/explore/synthetic-dataset.json \
		--run eval/fixtures/explore/perfect-run.json \
		--output-json /tmp/graphrag-explore-smoke.json >/dev/null

# Live: CHAMA O LLM e precisa dos serviços — roda dentro do container, como o
# eval-answer. Os testes de componente vão junto: eles precisam do Haystack.
eval-explore:
	docker exec hayhooks rm -rf /app/eval /app/tests
	docker cp eval hayhooks:/app/eval
	docker cp tests hayhooks:/app/tests
	docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \
		$(PYTHON) -m unittest tests.test_explore_components
	docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \
		$(PYTHON) -m eval.explore.live \
		--dataset "/app/$(EXPLORE_DATASET)" --output /tmp/explore-run.json
	docker cp hayhooks:/tmp/explore-run.json "$(EXPLORE_RUN)"
	$(MAKE) explore-score

# Pontuação offline do run coletado — reprocessável sem gastar LLM de novo.
explore-score:
	$(PYTHON) -m eval.explore.cli score \
		--dataset "$(EXPLORE_DATASET)" --run "$(EXPLORE_RUN)" \
		--output-json "$(EXPLORE_REPORT)" --output-md "$(EXPLORE_MARKDOWN)"

# Política de aceite. Com gold em `draft` o resultado sai marcado como NÃO
# conclusivo de propósito: gabarito não revisado não reprova nem aprova ninguém.
explore-gate:
	$(PYTHON) -m eval.explore.cli gate \
		--candidate "$(EXPLORE_REPORT)" --policy "$(EXPLORE_POLICY)" \
		$(if $(EXPLORE_BASELINE),--baseline "$(EXPLORE_BASELINE)",) \
		--output-json eval/reports/explore-gate.json

eval-full:
	$(MAKE) -C eval full \
		TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)" \
		BASELINE="$(or $(EVAL_BASELINE),eval/baselines/mcid-retrieval-v1.json)"

# Mapa de arquitetura (docs/arquitetura/index.html) como site estático.
# docs-build gera site/; docs-image empacota em nginx para subir atrás do domínio.
# O MkDocs vive num virtualenv próprio, criado no primeiro uso.
DOCS_VENV := .venv-docs
MKDOCS := $(DOCS_VENV)/bin/mkdocs

$(MKDOCS): requirements-docs.txt
	$(PYTHON) -m venv $(DOCS_VENV)
	$(DOCS_VENV)/bin/pip install -q -r requirements-docs.txt
	@touch $(MKDOCS)

docs-build: $(MKDOCS)
	$(MKDOCS) build --strict

docs-serve: $(MKDOCS)
	$(MKDOCS) serve -a localhost:8001

docs-image:
	docker build -f Dockerfile.docs -t graphrag-tais/arquitetura .

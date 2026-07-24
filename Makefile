RAG_SERVICES := qdrant neo4j hayhooks postgres-mcp

.PHONY: up eval-quick eval-score eval-full
up:
	docker compose up -d --build --no-deps $(RAG_SERVICES)

eval-quick:
	$(MAKE) -C eval quick

eval-score:
	$(MAKE) -C eval score \
		TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)"

eval-full:
	$(MAKE) -C eval full \
		TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)" \
		BASELINE="$(or $(EVAL_BASELINE),eval/baselines/mcid-retrieval-v1.json)"

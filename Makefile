RAG_SERVICES := qdrant neo4j minio hayhooks postgres-mcp

.PHONY: up eval-quick eval-collect eval-live eval-score eval-full
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

eval-full:
	$(MAKE) -C eval full \
		TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)" \
		BASELINE="$(or $(EVAL_BASELINE),eval/baselines/mcid-retrieval-v1.json)"

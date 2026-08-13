RAG_SERVICES := qdrant neo4j minio hayhooks postgres-mcp

.PHONY: up eval-quick eval-collect eval-live eval-score eval-full eval-answer
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

eval-full:
	$(MAKE) -C eval full \
		TRACE="$(or $(EVAL_TRACE),eval/reports/current-run.json)" \
		DATASET="$(or $(EVAL_DATASET),eval/datasets/retrieval/mcid-v0.1.0.json)" \
		BASELINE="$(or $(EVAL_BASELINE),eval/baselines/mcid-retrieval-v1.json)"

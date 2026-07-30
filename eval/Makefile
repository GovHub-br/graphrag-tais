PYTHON ?= python3

DATASET ?= eval/datasets/retrieval/mcid-v0.1.0.json
TRACE ?= eval/reports/current-run.json
POLICY ?= eval/config/retrieval-policy-v0.1.0.json
BASELINE ?= eval/baselines/mcid-retrieval-v1.json
CANDIDATE_REPORT ?= eval/reports/candidate.json
CANDIDATE_MARKDOWN ?= eval/reports/candidate.md
GATE_REPORT ?= eval/reports/gate.json
GATE_MARKDOWN ?= eval/reports/gate.md

SMOKE_DATASET := eval/fixtures/retrieval/synthetic-dataset.json
SMOKE_RUN := eval/fixtures/retrieval/perfect-run.json
SMOKE_REPORT := /tmp/graphrag-retrieval-smoke.json
SMOKE_MARKDOWN := /tmp/graphrag-retrieval-smoke.md
SMOKE_GATE := /tmp/graphrag-retrieval-smoke-gate.json

.PHONY: validate unit smoke quick score gate full

validate:
	cd .. && $(PYTHON) -m eval.retrieval.cli validate \
		--dataset "$(DATASET)"

unit:
	cd .. && $(PYTHON) -m unittest \
		tests.test_retrieval_eval \
		tests.test_live_retrieval \
		-v

smoke:
	cd .. && $(PYTHON) -m eval.retrieval.cli score \
		--dataset "$(SMOKE_DATASET)" \
		--run "$(SMOKE_RUN)" \
		--output-json "$(SMOKE_REPORT)" \
		--output-md "$(SMOKE_MARKDOWN)" >/dev/null
	cd .. && $(PYTHON) -m eval.retrieval.cli gate \
		--baseline "$(SMOKE_REPORT)" \
		--candidate "$(SMOKE_REPORT)" \
		--policy "$(POLICY)" \
		--output-json "$(SMOKE_GATE)" >/dev/null

quick: validate unit smoke

score:
	cd .. && $(PYTHON) -m eval.retrieval.cli score \
		--dataset "$(DATASET)" \
		--run "$(TRACE)" \
		--output-json "$(CANDIDATE_REPORT)" \
		--output-md "$(CANDIDATE_MARKDOWN)"

gate:
	cd .. && $(PYTHON) -m eval.retrieval.cli gate \
		--baseline "$(BASELINE)" \
		--candidate "$(CANDIDATE_REPORT)" \
		--policy "$(POLICY)" \
		--output-json "$(GATE_REPORT)" \
		--output-md "$(GATE_MARKDOWN)"

full: quick score gate

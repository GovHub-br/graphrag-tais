#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATASET_INPUT="${EVAL_DATASET:-eval/datasets/retrieval/mcid-business-v0.1.0.json}"
TRACE_OUTPUT="${EVAL_TRACE:-eval/reports/current-run.json}"
CONTAINER_NAME="${EVAL_CONTAINER:-hayhooks}"
RETRIEVAL_CHANNEL="${EVAL_CHANNEL:-hybrid}"
CONTAINER_WORKDIR="/tmp/graphrag-retrieval-eval"

if [[ "${DATASET_INPUT}" = /* ]]; then
  DATASET_PATH="${DATASET_INPUT}"
else
  DATASET_PATH="${PROJECT_ROOT}/${DATASET_INPUT}"
fi
if [[ "${TRACE_OUTPUT}" = /* ]]; then
  TRACE_PATH="${TRACE_OUTPUT}"
else
  TRACE_PATH="${PROJECT_ROOT}/${TRACE_OUTPUT}"
fi

if [[ ! -f "${DATASET_PATH}" ]]; then
  echo "Dataset não encontrado: ${DATASET_PATH}" >&2
  exit 2
fi
if ! docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "Container não encontrado: ${CONTAINER_NAME}" >&2
  exit 2
fi

GIT_COMMIT="$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
if [[ -n "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=normal)" ]]; then
  WORKTREE_STATE="dirty"
else
  WORKTREE_STATE="clean"
fi
CODE_TREE_HASH="$(
  cd "${PROJECT_ROOT}"
  # Only executable retrieval/evaluator code and gate configuration belong to
  # the code revision. Datasets and experiment records have their own hashes;
  # including a record that is updated after the run makes reproduction
  # impossible without changing the retrieval implementation.
  rg --files src/rag eval/retrieval eval/config \
    | sort \
    | while IFS= read -r source_file; do sha256sum "${source_file}"; done \
    | sha256sum \
    | cut -d' ' -f1
)"
CODE_REVISION="${GIT_COMMIT}+tree:${CODE_TREE_HASH}"

docker exec "${CONTAINER_NAME}" mkdir -p \
  "${CONTAINER_WORKDIR}/eval" \
  "${CONTAINER_WORKDIR}/output"
docker cp "${PROJECT_ROOT}/eval/." \
  "${CONTAINER_NAME}:${CONTAINER_WORKDIR}/eval/"

case "${DATASET_PATH}" in
  "${PROJECT_ROOT}/eval/"*)
    DATASET_RELATIVE="${DATASET_PATH#"${PROJECT_ROOT}/eval/"}"
    CONTAINER_DATASET="${CONTAINER_WORKDIR}/eval/${DATASET_RELATIVE}"
    ;;
  *)
    CONTAINER_DATASET="${CONTAINER_WORKDIR}/input-dataset.json"
    docker cp "${DATASET_PATH}" \
      "${CONTAINER_NAME}:${CONTAINER_DATASET}"
    ;;
esac

CONTAINER_TRACE="${CONTAINER_WORKDIR}/output/current-run.json"
docker exec \
  -e "PYTHONPATH=/app/src:${CONTAINER_WORKDIR}" \
  -e "RETRIEVAL_CODE_REVISION=${CODE_REVISION}" \
  -e "RETRIEVAL_WORKTREE_STATE=${WORKTREE_STATE}" \
  -e "RETRIEVAL_ENVIRONMENT_CLASS=docker-compose-local" \
  "${CONTAINER_NAME}" \
  python -m eval.retrieval.live \
  --dataset "${CONTAINER_DATASET}" \
  --channel "${RETRIEVAL_CHANNEL}" \
  --output "${CONTAINER_TRACE}"

mkdir -p "$(dirname "${TRACE_PATH}")"
docker cp "${CONTAINER_NAME}:${CONTAINER_TRACE}" "${TRACE_PATH}"
echo "RetrievalRun salvo em ${TRACE_PATH}"

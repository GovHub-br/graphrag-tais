#!/usr/bin/env bash
# Nomes de exibição dos modelos no open-webui (ADR-0014).
#
# POR QUE ISTO EXISTE: o Hayhooks deriva o id do modelo do NOME DO DIRETÓRIO da
# pipeline (`pipeline_name = pipeline_dir.name`), sem hook de override. Renomear
# o diretório para "Indicador"/"Explorar" mudaria as rotas HTTP (`/query/run`) e
# quebraria o histórico do open-webui, que guarda o id nas conversas. Então o
# código mantém `query`/`explore` e o nome bonito é aplicado na camada do
# open-webui — que é estado do BANCO dele, não do repositório.
#
# Este script torna esse estado reproduzível: rode depois de subir um
# open-webui novo (ou depois de limpar o volume `open-webui`). É idempotente.
#
#   ./scripts/setup_webui_models.sh [http://localhost:3000]
set -euo pipefail

WEBUI="${1:-${WEBUI_URL:-http://localhost:3000}}"

# Com WEBUI_AUTH=false o signin sem credenciais devolve o admin padrão. Com auth
# ligado, exporte WEBUI_EMAIL/WEBUI_PASSWORD.
TOKEN="$(curl -sf --max-time 15 -X POST "${WEBUI}/api/v1/auths/signin" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${WEBUI_EMAIL:-}\",\"password\":\"${WEBUI_PASSWORD:-}\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')"

aplicar() {
  local id="$1" nome="$2" desc="$3"
  local payload
  payload="$(python3 - "$id" "$nome" "$desc" <<'PY'
import json, sys
id_, nome, desc = sys.argv[1:4]
print(json.dumps({
    "id": id_, "base_model_id": None, "name": nome,
    "meta": {"description": desc, "profile_image_url": "/static/favicon.png"},
    # `access_grants` precisa ser LISTA: o endpoint de update reconstrói o
    # ModelForm e `None` falha a validação com list_type (HTTP 500 opaco).
    "params": {}, "access_grants": [], "is_active": True,
}))
PY
)"
  # `create` falha se a entrada já existe; nesse caso, atualiza.
  if curl -sf --max-time 15 -o /dev/null -X POST "${WEBUI}/api/v1/models/create" \
       -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
       -d "${payload}"; then
    echo "  criado:     ${id} -> ${nome}"
  else
    curl -sf --max-time 15 -o /dev/null -X POST \
      "${WEBUI}/api/v1/models/model/update?id=${id}" \
      -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
      -d "${payload}"
    echo "  atualizado: ${id} -> ${nome}"
  fi
}

echo "Configurando nomes de exibição em ${WEBUI}"
aplicar query "Indicador" \
  "Mede um indicador: SQL executado, gráfico e relatório .docx. Para quem já sabe o que quer medir."
aplicar explore "Explorar" \
  "Mostra o que existe no catálogo: bases, tabelas, o que dá para perguntar e o que não existe. Para quem está começando."

echo
echo "Dropdown resultante:"
curl -sf --max-time 15 -H "Authorization: Bearer ${TOKEN}" "${WEBUI}/api/models" \
  | python3 -c '
import sys, json
for m in json.load(sys.stdin)["data"]:
    print("  {:<12} -> {}".format(m["id"], m.get("name")))
'

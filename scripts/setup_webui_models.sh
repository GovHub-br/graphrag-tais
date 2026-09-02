#!/usr/bin/env bash
# Dropdown de modelos do open-webui: nomes, descrições e o que NÃO aparece
# (ADR-0014).
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
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

# Com WEBUI_AUTH=false o signin sem credenciais devolve o admin padrão. Com auth
# ligado, exporte WEBUI_EMAIL/WEBUI_PASSWORD.
TOKEN="$(curl -sf --max-time 15 -X POST "${WEBUI}/api/v1/auths/signin" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${WEBUI_EMAIL:-}\",\"password\":\"${WEBUI_PASSWORD:-}\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')"

# $4 = visibilidade ("true"/"false"). Em utils/models.py, uma entrada com
# base_model_id nulo casa com o modelo base de mesmo id e decide o destino dele:
# is_active=true renomeia (`model['name'] = custom_model.name`), is_active=false
# o remove da lista (`models.remove(model)`). É o gancho oficial para esconder um
# modelo da UI sem mexer no Hayhooks — a pipeline segue registrada e a rota
# `/<id>/run` continua respondendo.
#
# $5 = avatar da persona (PNG do repo).
# ⚠️ O avatar vai EMBUTIDO em base64, e não como caminho `/static/...`: os
# ícones das personas não estão entre os arquivos que o compose monta em
# `/app/build/static`. E como este payload substitui `meta` INTEIRO, um
# `aplicar` sem avatar APAGA o que estava lá — o ícone da persona vira a marca
# genérica na tela inicial e no dropdown.
aplicar() {
  local id="$1" nome="$2" desc="$3" ativo="${4:-true}" icone="${5:-}"
  local payload
  payload="$(python3 - "$id" "$nome" "$desc" "$ativo" "$icone" <<'PY'
import base64, json, sys
id_, nome, desc, ativo, icone = sys.argv[1:6]
if icone:
    with open(icone, "rb") as fh:
        imagem = "data:image/png;base64," + base64.b64encode(fh.read()).decode()
else:
    imagem = "/static/favicon.png"
print(json.dumps({
    "id": id_, "base_model_id": None, "name": nome,
    "meta": {"description": desc, "profile_image_url": imagem},
    # `access_grants` precisa ser LISTA: o endpoint de update reconstrói o
    # ModelForm e `None` falha a validação com list_type (HTTP 500 opaco).
    "params": {}, "access_grants": [], "is_active": ativo == "true",
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

# A DESCRIÇÃO É A MENSAGEM DE BOAS-VINDAS. Na tela inicial ela aparece entre o
# nome da persona e a barra de digitação (`Placeholder.svelte`), que é exatamente
# onde uma saudação cabe — e no dropdown ela só sai no tooltip do ℹ️, então o tom
# de conversa não polui a escolha do modelo. Dois limites do componente:
#   • `line-clamp-2` + `max-w-xl` → o que passar de DUAS linhas (~170 caracteres)
#     é cortado com reticências, visível só no hover;
#   • o que a tela já diz não se repete aqui: "escreva na barra abaixo" é o
#     placeholder do input, e o convite a começar são os cards de "Sugerido".
echo "Configurando dropdown em ${WEBUI}"
aplicar query "Indicador" \
  "Olá! Vou te acompanhar na medição: me diga o que você quer medir e eu devolvo as bases usadas, o SQL executado, a leitura do resultado, o gráfico e o relatório .docx." \
  true "${RAIZ}/webui/assets/govhub-icon-indicador.png"
aplicar explore "Explorar" \
  "Olá! Vou te acompanhar na exploração: mostro as bases e as tabelas disponíveis, o grão de cada uma, uma amostra de linhas reais e também o que ainda não existe." \
  true "${RAIZ}/webui/assets/govhub-icon-explorar.png"

# `ingest` é operação, não persona: quem usa o assistente não tem o que fazer
# com ela. Sai do dropdown, mas continua registrada no Hayhooks — a ingestão
# roda por `POST /ingest/run`, sem passar pelo open-webui.
aplicar ingest "ingest" \
  "Pipeline de ingestão — operacional, oculta da UI." false \
  "${RAIZ}/webui/assets/govhub-icon-ingest.png"

# O "Arena Model" é um modelo sintético que o próprio open-webui injeta para
# comparar respostas lado a lado (utils/models.py: `if config.get(
# 'evaluation.arena.enable')`). Não é persona do GovHub e confunde.
# ⚠️ Isto é config do BANCO: o env ENABLE_EVALUATION_ARENA_MODELS do compose só
# vale em instalação nova, porque o valor salvo vence.
curl -sf --max-time 15 -o /dev/null -X POST "${WEBUI}/api/v1/evaluations/config" \
  -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
  -d '{"ENABLE_EVALUATION_ARENA_MODELS": false}'
echo "  oculto:     arena-model"

# --------------------------------------------------------------------------- #
# Perguntas sugeridas da tela inicial.
# --------------------------------------------------------------------------- #
# Mesma armadilha do Arena: `ui.prompt_suggestions` é PersistentConfig. Se o
# open-webui subiu UMA vez sem DEFAULT_PROMPT_SUGGESTIONS, ele gravou os
# exemplos em inglês dele no banco — e daí em diante o valor salvo vence o env
# para sempre, sem erro no log. Foi o que aconteceu em prod: o container subiu
# antes de a variável entrar no compose (`config_old` vazia confirma que nunca
# houve outro valor gravado). Este passo sobrescreve o banco.
#
# Fonte única = o env do compose. O script não redigita as perguntas: lê a
# variável do ambiente, e como conveniência tenta o próprio container.
SUG="${DEFAULT_PROMPT_SUGGESTIONS:-}"
if [ -z "$SUG" ] && command -v docker >/dev/null 2>&1; then
  SUG="$(docker exec open-webui printenv DEFAULT_PROMPT_SUGGESTIONS 2>/dev/null || true)"
fi

if [ -n "$SUG" ]; then
  SUG_PAYLOAD="$(printf '%s' "$SUG" | python3 -c 'import json,sys; print(json.dumps({"suggestions": json.load(sys.stdin)}, ensure_ascii=False))')"
  curl -sf --max-time 15 -o /dev/null -X POST "${WEBUI}/api/v1/configs/suggestions" \
    -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
    -d "${SUG_PAYLOAD}"
  echo "  sugestoes:  aplicadas do DEFAULT_PROMPT_SUGGESTIONS"
else
  echo "  sugestoes:  PULADO (defina DEFAULT_PROMPT_SUGGESTIONS ou rode na VM)"
fi

echo
echo "Dropdown resultante:"
curl -sf --max-time 15 -H "Authorization: Bearer ${TOKEN}" "${WEBUI}/api/models" \
  | python3 -c '
import sys, json
for m in json.load(sys.stdin)["data"]:
    print("  {:<12} -> {}".format(m["id"], m.get("name")))
'

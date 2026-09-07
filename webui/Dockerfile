# Open WebUI com a identidade visual GovHub ja embutida na imagem.
#
# Por que existe: ate aqui os arquivos do tema entravam por 15 bind mounts no
# docker-compose.yaml, o que exige o repositorio clonado ao lado do container.
# Num deploy sem o repo (servidor, k8s) o tema simplesmente nao aparecia.
#
# Nao e fork nem rebuild do Open WebUI: e uma camada de COPY por cima da imagem
# oficial, usando os mesmos dois ganchos de sempre (custom.css + loader.js).
#
# Destino /app/build/static: e a FONTE, nao backend/open_webui/static. No boot o
# config.py apaga o segundo e o recopia do primeiro — mesmo motivo dos mounts
# antigos, documentado em webui/README.md.
#
# A tag base fica pinada de proposito (ver o comentario no docker-compose.yaml):
# o CSS depende dos design tokens desta build, e o Alembic do Open WebUI nao
# sabe DESCER de revisao. Ao mexer aqui, so suba de versao.
ARG OPEN_WEBUI_TAG=v0.11.0
FROM ghcr.io/open-webui/open-webui:${OPEN_WEBUI_TAG}

COPY custom.css loader.js /app/build/static/
COPY assets/ /app/build/static/

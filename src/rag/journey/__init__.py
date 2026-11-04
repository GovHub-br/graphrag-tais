"""Jornada do usuário (ADR-0020): uma pergunta, uma resposta simples, botões.

A pessoa não escolhe persona. A primeira resposta já sai no formato da
**População geral** (uma frase com o número, o número em escala humana, um mapa
ou uma comparação e a fonte). Debaixo dela, a interface mostra **botões** para as
personas específicas — técnica, especialista em políticas e gestor — e para
"Contar a história" e "Baixar". Cada clique abre uma trilha com entregas
próprias.

Este pacote é a regra da jornada, separada de quem a serve:

- `tracks`           catálogo das trilhas (botão, persona, palpite de ordem);
- `routing`          número ou assunto — o primeiro desvio do fluxo;
- `marker`           estado escondido na resposta (a conversa é a memória);
- `openwebui_tasks`  pedidos de bastidor do OpenWebUI (follow-ups, título, tags);
- `facts`            o que o resultado executado diz, calculado em Python;
- `human_scale`      o número em escala humana, a partir de tabela fixa;
- `catalog_probe`    o que o catálogo tem (e não tem) para as trilhas;
- `render`           o Markdown de cada trilha;
- `service`          a orquestração, com o motor e o executor injetados;
- `state`            o resultado de cada resposta, guardado para os cliques.

Quem expõe isso como modelo do OpenWebUI é `pipelines/jornada`. O caminho
medido da Persona 2 (`pipelines/query`, `pipelines/delivery`) é **reusado, não
alterado** — mesma regra 1 do ADR-0014.
"""

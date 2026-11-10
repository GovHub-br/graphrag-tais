# Produção MCid (work-minc) atualizada e testada contra o banco real — 09/11/2026

- **Deploy**: `work-minc` (10.0.0.210), `/home/lappis/graphrag-tais`, projeto compose
  `graphrag-tais` (`docker-compose.yaml` + `compose.mcid.yaml`), escopo `mcid`.
- **Atualização**: o repo estava em `7e37ff7` (25/08) com 68 arquivos copiados por fora
  do git. Todos eram versões de arquivos que já estão no histórico (43 idênticos à `main`,
  14 de commits antigos, 2 versões velhas sem valor). Os únicos arquivos próprios da VM
  (`compose.mcid.yaml`, o `.bak` e `docker-compose.override.yml`) e o `.env` foram
  mantidos. Backup em `/root/backup-graphrag-tais-20261109/` (patch + tar + HEAD antigo).
  `git reset --hard origin/main` (`79be296`). Rebuild só de `hayhooks-mcid` e
  `open-webui-mcid`, depois `setup_webui_models.sh` (Assistente padrão, botões ligados).
  O MinC (`/home/lappis/minc`) e os bancos não foram tocados.
- **Run**: `eval/reports/journey-prod-20261109/` (fora do git): `run.json`, transcrições,
  `gabarito.md`/`gabarito.csv`, `catalogo-x-banco.json`, `smoke_personas.txt`.
- **Dataset**: `cidades-journey-v0.2.0`. Os 13 casos de número ganharam **gabarito**: SQL
  escrito à mão e rodado ao vivo no banco, com leituras alternativas e checagens do dado
  (`eval/journey/truth.py`).

## 1. Esperado (banco) × recebido (sistema)

| caso | banco | sistema (na tela) | veredito | de quem é |
|---|---|---|---|---|
| FAR empreendimentos por UF | 878 | 878 — 27/27 UFs batem | ok | — |
| FAR valor por UF | R$ 24,96 bi | R$ 25 bi — 27/27 UFs | ok | — |
| MCMV Rural pessoas por região | 835.032 | 835 mil — 5/5 regiões | ok | — |
| Poupança 2025 mês a mês | R$ 4,49 bi (dez) | R$ 4,5 bi — 12/12 meses | ok | — |
| CAGED empregos formais | 3.119.312 (estoque) | 14,1 mil (saldo do mês) | leitura alternativa | interpretação |
| FDS top 10 EOs | líder 3.173 | 10,9 mil (soma do top 10) | leitura alternativa | sistema (destaque de ranking) |
| INCC-M 12 meses 2025 | 6,10% | "0,1" | **diferente** (fator 1/100) | **dado + sistema** |
| OGU pago por ação | R$ 19,5 bi | R$ 39 bi / R$ 40,5 bi* | **diferente** (dobrado) | **dado + sistema** |
| FipeZap último mês | 289,81 (jul/26) | sem número | sem número | **dado + sistema** |
| FAR total de UH | 131.835 | sem número | sem número | **catálogo** (tabela fantasma) |
| Pró-Moradia por UF | R$ 5,84 bi | sem número | sem número | **catálogo** (tabela fantasma) |
| FNHIS Sub-50 por UF | 1.208 | sem número | sem número | **catálogo** (fora do escopo `mcid`) |
| TS fora do marco por UF | 2.332 | sem número | sem número | **catálogo** (fora do escopo `mcid`) |

\* O motor variou entre `SUM(pagamento)` (R$ 39 bi, exatamente 2× o banco) e
`SUM(pag_total)` (R$ 40,5 bi, exatamente 2× o pago com restos a pagar). Nas duas, por ação
os valores batem (7/7). O erro é só a linha `SOMA` entrando na soma.

**Nenhum número inventado pelo LLM** (`phrase_with_numbers_outside_facts = 0`). Toda
diferença vem do dado, do catálogo ou de regra do `facts.py`.

## 2. Problemas no DADO e no CATÁLOGO (não no sistema)

Checados direto no banco, sem passar pelo RAG:

1. **17 tabelas do catálogo `mcid` não existem no banco** (`scripts/audit_catalog_vs_db.py`).
   São as famílias `*historico*` e `*reloginho*`, por exemplo `prata_far_historico_empreendimento`
   e `prata_historico_serie_executiva`, mais 3 tabelas com colunas inexistentes. O retrieval
   gosta delas, porque têm descrição rica. Derrubaram "total de UH do FAR", "Pró-Moradia
   por UF", a série da política do FDS, e a **amostra do Explorar para "o que existe sobre o
   FAR?"**, que além disso sugere perguntas sobre essas tabelas. No escopo `cidades` local
   são **90**, porque ainda há schemas `conjuntura_*` antigos misturados. Correção: na
   ingestão/catálogo, não no prompt.
2. **FNHIS, Pró-Moradia (panorama) e Trabalho Social (resumo por UF) estão fora do catálogo
   `mcid`.** O banco tem as tabelas `ouro_fnhis_funil_propostas`,
   `ouro_pro_moradia_panorama_estadual` e `ouro_ts_resumo_uf`. A resposta "não consegui" é
   honesta, mas é cobertura de catálogo.
3. **`ouro_conjuntura_ogu` tem a linha `SOMA`** (`UNION ALL` no dbt), e o catálogo diz "uma
   linha por ação".
4. **`ouro_conjuntura_incc_m.var_12_meses` está em fração**, e o catálogo diz "variação
   percentual". Na mesma tabela, `var_mes` está em %.
5. **O último mês do CAGED e do FipeZap vem com a métrica `NULL`** (jul/ago 2026). Um
   `ORDER BY data DESC LIMIT 1` devolve vazio, e foi isso que deixou o FipeZap sem número.
6. **`ouro_far_mapa_nacional` empilha `portaria = 'Todas as portarias'`** junto das
   portarias, e `nivel` uf + região. Somar a tabela inteira conta o FAR 2× ou 4×. Hoje o
   motor usou a ficha e acertou, mas é a próxima `SOMA`.

## 3. Problemas no SISTEMA

1. **A autocorreção qualifica a tabela com o FQN do catálogo** (`Cidades.cidades.prata.…`),
   e o Postgres recusa (`Failed to parse SQL statement`): foram 15 vezes no log do
   postgres-mcp durante os testes. O `query_system.md` tem a regra "nome físico, nunca o
   FQN"; o `sql_executor_system.md` não tem. O defeito apareceu agora porque a autocorreção
   voltou a funcionar (fix do MCP). Nos casos de hoje ele só mascarou tabela fantasma, mas
   vai derrubar correção que daria certo. Correção barata: a mesma regra no prompt do
   executor.
2. **A, B e G do baseline continuam**: `fmt` arredonda para 1 casa, a linha de total só é
   tirada da soma quando a dimensão é UF, e o destaque de ranking é a soma do top N.
3. **Ranking/série escolhem o destaque errado para a pergunta**: CAGED mostra o saldo
   quando a pergunta é "empregos formais", e FDS mostra a soma do top 10.
4. **Explorar, pergunta de catálogo**: o panorama agrupa por **camada** (bronze/prata/ouro),
   não por programa. Para quem não conhece as bases, "Camada bronze — dado bruto tipado" não
   orienta. O smoke esperava ≥ 5 domínios e vieram 3.
5. **Explorar**: a célula de grão é cortada em "…". O smoke cobra que não seja.

## 4. Deploy: configuração (decisão do Davi, nada alterado)

- **`PUBLIC_BASE_URL` ausente**: todo link de download sai `http://localhost:8000/...`. O
  arquivo existe e a assinatura vale (testado trocando o host: CSV e `.docx` respondem 200),
  mas ninguém baixa pelo navegador. O boot avisa no log.
- **Chave dos links é o default público**: sem `DOWNLOAD_SIGNING_KEY` e sem
  `MINIO_SECRET_KEY` no `.env`, o HMAC usa `minioadmin`. Quem souber disso forja link
  válido para qualquer objeto dos buckets, e o nome do CSV é previsível (pergunta +
  horário). Definir `DOWNLOAD_SIGNING_KEY` com um segredo resolve.
- O Open WebUI tenta o Ollama em `host.docker.internal:11434` a cada 10 min (ruído;
  `ENABLE_OLLAMA_API=false`).
- `minio-mcid` ainda roda `minio/minio:latest` (a `main` usa `pgsty/minio`). Não foi
  recriado de propósito: troca de imagem com dado em disco é outra mudança.

## 5. O que funciona em produção

- **Fix do MCP confirmado em prod**: a cópia por execução não fecha mais a sessão. Durante
  os testes, as autocorreções de `column "ano"` foram corrigidas e rodaram.
- **Rota**: 21/21. **Trilhas**: 52/52 abertas sem erro, sem botão repetido, sem emoji, sem
  exceção. **Guardrails**: CPF recusado, "o que você faz" apresentado. Respostas sem dado
  são honestas.
- **Caminho real pelo Open WebUI** (`:3010`, streaming): a 1ª resposta do Assistente sai em
  3,8 s, 190 KB, 3 imagens, linhas SSE ≤ 16 KB. O clique do gestor leva 1 s. O Indicador
  leva 4,2 s, com gráfico e SQL executado, mas manda uma linha SSE de 72 KB sem pedaços: hoje
  cabe no limite de 128 KB, e um gráfico maior não caberia.
- Latência: 1ª resposta p50 3,5 s, p95 11,7 s. Trilha de política p50 6,3 s, máx 19 s.
- `smoke_personas`: 23/29. As 6 falhas são: 2 falsos negativos (o script olha o Open WebUI
  na porta 3000, e o MCid está na 3010, onde os nomes estão certos), 3 por tabela fantasma
  na amostra do Explorar e 1 do panorama por camada.

## 6. Ordem sugerida

1. Catálogo: tirar do escopo `mcid` as 17 tabelas fantasmas e incluir FNHIS, Pró-Moradia e
   TS. Rodar `scripts/audit_catalog_vs_db.py` depois de cada ingestão.
2. `.env` da VM: `PUBLIC_BASE_URL` e `DOWNLOAD_SIGNING_KEY`.
3. `sql_executor_system.md`: regra do nome físico.
4. dbt: linha `SOMA` fora da tabela analítica; `var_12_meses`/`var_ano` em %; não publicar
   o mês com métrica vazia.
5. RAG: `fmt` com algarismos significativos, linha de total em categoria, destaque de
   ranking = líder. Depois, repetir `make eval-journey` e comparar o `gabarito.md`.

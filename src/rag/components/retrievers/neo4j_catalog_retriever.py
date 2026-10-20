"""Neo4jCatalogRetriever — o retriever da PERSONA 1 (explorar o catálogo).

A Persona 2 pergunta "quanto foi X por UF" e precisa da tabela CERTA; a Persona 1
pergunta "o que vocês têm sobre habitação" e precisa do MAPA. São objetivos
opostos de recuperação (precisão vs. cobertura), então este componente é
separado do `Neo4jSubgraphRetriever` — que continua servindo a Persona 2 sem
nenhuma alteração de comportamento (ADR-0014).

Três diferenças de fundo em relação ao retriever da Persona 2:

1. **Ordem = relevância.** Os documentos saem ordenados por relevância (tabelas
   semente na ordem do híbrido, depois os vizinhos), e não por FQN. A resposta da
   Persona 1 é um *ranking* de tabelas, e é esse ranking que o eval pontua
   (recall@k, MRR, nDCG) — ordenar por FQN destruiria a métrica.
2. **Expansão sempre ampla.** Mantém todos os vizinhos de 1 hop por
   `SEMANTIC_JOIN`, elegíveis ou não: "o que se conecta com isso" é parte da
   pergunta da Persona 1, e um join que exige normalização ainda é informação.
   (A Persona 2 usa `EXPANSION_MODE=evidence` justamente para o contrário.)
3. **Visão de catálogo.** Além do subgrafo, devolve o panorama de domínios
   (schema → nº de tabelas + nomes) por Cypher agregado. "Quais bases existem"
   é consulta de catálogo, não busca semântica: embedding responde mal a isso, e
   uma agregação determinística responde exatamente.

O `catalog` devolvido (todos os nomes físicos do escopo) é a âncora anti-alucinação:
o `ExploreAnswerBuilder` descarta qualquer tabela citada pelo LLM que não esteja nele.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List

from haystack import Document, component
from loguru import logger

from rag.components.graphstore.neo4j import Neo4jGraphStore

# Reuso deliberado (somente leitura) dos helpers do retriever da Persona 2: a
# tokenização da pergunta, a leitura do grão e o nome curto do schema devem ter
# UMA fonte da verdade. Importar evita divergência silenciosa entre as personas
# e não altera nada do caminho da Persona 2.
from rag.components.retrievers.neo4j_subgraph_retriever import (
    _grain_for,
    _schema_short,
    _tokenize,
)

# `_col_matches` da Persona 2 NÃO é reusado aqui — ver `_pontua_coluna`. Ele
# casa substring crua, o que nesta persona custava caro: numa pergunta sobre
# "localização", `municipio` (descrição: "…esta localizado") ficava de fora e
# `situacao_empreendimento` entrava por conter a palavra "dados". Como a
# Persona 2 é território medido, a correção nasce aqui e só depois (com o gate
# do ADR-0012) se decide levá-la para lá.

# ---------------------------------------------------------------------- #
# Relevância de coluna (Persona 1)
# ---------------------------------------------------------------------- #
# Palavras que aparecem em quase toda descrição de catálogo e em quase toda
# pergunta: casar por elas é ruído puro. Foi assim que `situacao_empreendimento`
# ("…priorizada a partir dos DADOS da CAIXA") entrou no card de uma pergunta
# sobre localização, e `municipio` ficou de fora.
_RUIDO_CATALOGO = {
    "dado", "dados", "tabela", "tabelas", "base", "bases", "banco", "campo",
    "campos", "coluna", "colunas", "registro", "registros", "informacao",
    "informacoes", "contem", "conter", "existe", "existem", "tenho", "quero",
    "consolida", "consolidado", "partir", "priorizada", "priorizando",
}

# Sufixos flexionais do português, do mais longo para o mais curto: o radical é
# o que faz "localização" (pergunta) encontrar "localizado" (descrição).
_SUFIXOS = (
    "mentos", "mento", "acoes", "acao", "coes", "idades", "idade", "ados",
    "adas", "idos", "idas", "ores", "ado", "ada", "ido", "ida", "oes", "eis",
    "es", "s",
)


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", (texto or "").lower())
        if not unicodedata.combining(c)
    )


def _radical(palavra: str) -> str:
    """Radical aproximado: tira o sufixo flexional mais longo que ainda deixa
    4 caracteres. Não é stemmer de verdade — é o suficiente para casar as
    flexões que aparecem em nome de coluna e em descrição de catálogo."""
    for sufixo in _SUFIXOS:
        if palavra.endswith(sufixo) and len(palavra) - len(sufixo) >= 4:
            return palavra[: -len(sufixo)]
    return palavra


def _radicais(texto: str) -> set:
    return {
        _radical(parte)
        for parte in re.split(r"[^0-9a-z]+", _sem_acento(texto))
        if len(parte) >= 4 and parte not in _RUIDO_CATALOGO
    }


def _radicais_da_pergunta(query: str) -> set:
    """Radicais úteis da pergunta. Reusa a tokenização da Persona 2 (stopwords
    gerais) e tira ainda as palavras genéricas de catálogo."""
    return {
        _radical(_sem_acento(token))
        for token in _tokenize(query)
        if _sem_acento(token) not in _RUIDO_CATALOGO
    }


def _casa_radical(radical: str, alvo: str) -> bool:
    """Dois radicais casam por igualdade ou por prefixo de 4. Aplicado DEPOIS da
    remoção de sufixo, o prefixo curto erra pouco: `rend` liga renda/rendimento,
    e as palavras genéricas que ele ligaria por acaso já saíram no ruído."""
    return radical == alvo or (min(len(radical), len(alvo)) >= 4
                               and radical[:4] == alvo[:4])


def _pontua_colunas(all_cols: list, termos: set) -> Dict[str, float]:
    """Relevância de cada coluna para a pergunta, por sobreposição de radicais
    **ponderada por raridade dentro da tabela** (IDF local).

    A raridade é o que separa sinal de ruído sem dicionário nenhum: numa tabela
    de empreendimentos, "empreendimento" aparece na descrição de quase toda
    coluna e não distingue nada, enquanto "localiz" aparece em duas — e são
    exatamente as duas que a pergunta queria. O nome da coluna pesa mais que a
    descrição: quem se chama `municipio` fala de município; quem só menciona a
    palavra na prosa, talvez."""
    if not termos:
        return {}

    nomes = {c["name"]: _radicais(c["name"]) for c in all_cols}
    descricoes = {c["name"]: _radicais(c.get("description", "")) for c in all_cols}

    casados: Dict[str, set] = {}
    for termo in termos:
        atingidas = {
            col["name"] for col in all_cols
            if any(_casa_radical(termo, r) for r in nomes[col["name"]] | descricoes[col["name"]])
        }
        if atingidas:
            casados[termo] = atingidas

    pontos: Dict[str, float] = {}
    for termo, atingidas in casados.items():
        raridade = 1.0 / len(atingidas)          # IDF local, sem log: tabela é pequena
        for nome in atingidas:
            peso = 3.0 if any(_casa_radical(termo, r) for r in nomes[nome]) else 1.0
            pontos[nome] = pontos.get(nome, 0.0) + raridade * peso
    return pontos


# Panorama do catálogo: um registro por schema, com contagem e nomes das tabelas.
# É o bloco 1 da resposta e a fonte do `catalog` (âncora anti-alucinação).
_DOMAINS_CYPHER = """
MATCH (t:Table)
WHERE $scope_id IS NULL OR t.scope_id = $scope_id
WITH t.schema AS schema_fqn, collect(t.name) AS names
RETURN schema_fqn, size(names) AS table_count, names
ORDER BY schema_fqn
"""

# Resolve as sementes para TABELAS preservando a ordem do ranking híbrido:
# `min(i)` guarda a melhor posição em que aquela tabela apareceu.
_SEED_TABLES_CYPHER = """
UNWIND range(0, size($ids) - 1) AS i
WITH i, $ids[i] AS nid
MATCH (seed {node_id: nid})
OPTIONAL MATCH (seed)<-[:HAS_COLUMN]-(parent:Table)
WITH i, coalesce(CASE WHEN seed:Table THEN seed.node_id END, parent.node_id) AS tid
WHERE tid IS NOT NULL
RETURN tid, min(i) AS rank
ORDER BY rank
"""

# Vizinhos de 1 hop por SEMANTIC_JOIN, herdando a posição da melhor semente que
# os puxou (`rank`) e contando por quantas sementes foram alcançados (`links`).
_NEIGHBORS_CYPHER = """
UNWIND range(0, size($tids) - 1) AS i
WITH i, $tids[i] AS tid
MATCH (t:Table {node_id: tid})-[:HAS_COLUMN]->(:Column)
      -[:SEMANTIC_JOIN]-(:Column)<-[:HAS_COLUMN]-(nbr:Table)
WHERE nbr.node_id <> tid
RETURN nbr.node_id AS neighbor_id, min(i) AS rank, count(DISTINCT tid) AS links
ORDER BY rank, neighbor_id
"""

_TABLES_CYPHER = """
UNWIND $tids AS tid
MATCH (tab:Table {node_id: tid})-[:HAS_COLUMN]->(col:Column)
WITH tab, col ORDER BY col.ordinal_position
RETURN tab.node_id AS table_fqn, tab.name AS table_name, tab.schema AS schema_fqn,
       tab.description AS table_desc, tab.grain AS table_grain,
       collect({
         name: col.name,
         type: coalesce(col.data_type_display, col.data_type),
         is_pk: col.is_primary_key,
         description: col.description
       }) AS columns
"""

_JOINS_CYPHER = """
UNWIND $tids AS tid
MATCH (t:Table {node_id: tid})-[:HAS_COLUMN]->(c1:Column)
      -[r:SEMANTIC_JOIN]-(c2:Column)<-[:HAS_COLUMN]-(t2:Table)
WHERE t2.node_id IN $tids AND t.node_id < t2.node_id
RETURN DISTINCT
       t.node_id AS left_table_fqn, t.name AS left_table, c1.name AS left_col,
       t2.node_id AS right_table_fqn, t2.name AS right_table, c2.name AS right_col,
       r.join_rule AS join_rule, r.sql_join_eligible AS sql_join_eligible,
       r.cardinality AS cardinality
ORDER BY left_table, right_table
"""


_DOMAIN_LABELS_CACHE: dict | None = None


def _domain_label(schema_short: str) -> str:
    """Rótulo de negócio do schema (`OM_DOMAIN_LABELS`), com fallback no nome.

    A Persona 1 pensa em "MCMV Entidades", não em `entidades_fds`. Sem mapa
    configurado devolve o nome curto, que já funciona em qualquer catálogo.
    """
    global _DOMAIN_LABELS_CACHE
    if _DOMAIN_LABELS_CACHE is None:
        from rag.settings.configs import settings

        raw = (settings.OM_DOMAIN_LABELS or "").strip()
        try:
            _DOMAIN_LABELS_CACHE = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            logger.warning("OM_DOMAIN_LABELS não é JSON válido; usando nome do schema")
            _DOMAIN_LABELS_CACHE = {}
    return _DOMAIN_LABELS_CACHE.get(schema_short, schema_short)


@component
class Neo4jCatalogRetriever:
    """Monta a visão de catálogo da Persona 1 a partir das sementes do híbrido."""

    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        scope_id: str | None = None,
        max_seeds: int = 15,
        max_tables: int = 25,
        max_columns: int = 8,
        max_columns_expansion: int | None = None,
        expand_neighbors: bool = True,
    ):
        self.store = graph_store
        self.scope_id = scope_id
        self.max_seeds = max_seeds
        self.max_tables = max_tables
        self.max_columns = max_columns
        self.max_columns_expansion = (max_columns if max_columns_expansion is None
                                      else max_columns_expansion)
        self.expand_neighbors = expand_neighbors

    @component.output_types(
        documents=List[Document],
        joins=List[Dict[str, Any]],
        domains=List[Dict[str, Any]],
        catalog=List[str],
    )
    def run(self, documents: List[Document] | None = None, query: str = "") -> dict:
        domains, catalog = self._catalog_overview()

        seed_ids: List[str] = []
        seed_column_fqns: set = set()
        for doc in documents or []:
            nid = (doc.meta or {}).get("node_id")
            if not nid or nid in seed_ids:
                continue
            seed_ids.append(nid)
            if (doc.meta or {}).get("card_type") != "table":
                seed_column_fqns.add(nid)
        seed_ids = seed_ids[: self.max_seeds]

        if not seed_ids:
            # Pergunta de catálogo puro ("quais bases existem?"): sem semente, a
            # resposta é o panorama — que já está pronto e é determinístico.
            logger.info("Neo4jCatalogRetriever: sem sementes; devolvendo só o panorama")
            return {"documents": [], "joins": [], "domains": domains, "catalog": catalog}

        ranked_ids = self._ranked_table_ids(seed_ids)
        seed_table_fqns = {tid for tid, _, is_seed in ranked_ids if is_seed}
        table_ids = [tid for tid, _, _ in ranked_ids][: self.max_tables]

        rows = {
            row["table_fqn"]: row
            for row in self.store.execute_read(_TABLES_CYPHER, {"tids": table_ids})
        }
        joins = self.store.execute_read(_JOINS_CYPHER, {"tids": table_ids}) if table_ids else []

        join_key_cols: Dict[str, set] = {}
        joins_by_table: Dict[str, List[dict]] = {}
        for j in joins:
            join_key_cols.setdefault(j["left_table_fqn"], set()).add(j["left_col"])
            join_key_cols.setdefault(j["right_table_fqn"], set()).add(j["right_col"])
            joins_by_table.setdefault(j["left_table_fqn"], []).append(j)
            joins_by_table.setdefault(j["right_table_fqn"], []).append(j)

        query_tokens = _radicais_da_pergunta(query)
        out: List[Document] = []
        for table_fqn in table_ids:  # a ORDEM aqui é a relevância — não reordenar
            row = rows.get(table_fqn)
            if not row:
                continue
            schema = _schema_short(row.get("schema_fqn"), table_fqn)
            sql_name = f"{schema}.{row['table_name']}" if schema else row["table_name"]
            all_cols = [c for c in (row.get("columns") or []) if c and c.get("name")]
            kept = self._select_columns(
                all_cols,
                table_fqn=table_fqn,
                seed_column_fqns=seed_column_fqns,
                join_key_names=join_key_cols.get(table_fqn, set()),
                query_tokens=query_tokens,
                is_seed_table=table_fqn in seed_table_fqns,
            )
            neighbors = self._neighbor_labels(table_fqn, joins_by_table.get(table_fqn, []))
            mantidas = {c["name"] for c in kept}
            e_semente = table_fqn in seed_table_fqns
            omitidas = ([c["name"] for c in all_cols if c["name"] not in mantidas]
                        if e_semente else [])
            out.append(Document(
                content=self._serialize(row, sql_name, schema, kept, neighbors, omitidas),
                meta={
                    "node_id": table_fqn,
                    "fqn": table_fqn,
                    "label": "Table",
                    "schema": schema,
                    "domain": _domain_label(schema),
                    "table": row["table_name"],
                    "sql_name": sql_name,
                    "description": row.get("table_desc") or "",
                    "grain": _grain_for(table_fqn, row.get("table_grain")),
                    "is_seed": table_fqn in seed_table_fqns,
                    "is_island": not neighbors,
                    "neighbors": neighbors,
                    "omitted_column_count": len(all_cols) - len(kept),
                    "columns": [
                        {
                            "name": c["name"],
                            "type": c.get("type") or "",
                            "is_pk": bool(c.get("is_pk")),
                            "description": c.get("description") or "",
                        }
                        for c in kept
                    ],
                },
            ))

        logger.info(
            "Neo4jCatalogRetriever: {} sementes → {} tabelas ({} semente, {} expansão), "
            "{} joins, {} domínios",
            len(seed_ids), len(out), len(seed_table_fqns),
            max(0, len(out) - len(seed_table_fqns)), len(joins), len(domains),
        )
        return {"documents": out, "joins": joins, "domains": domains, "catalog": catalog}

    # ------------------------------------------------------------------ #
    def _catalog_overview(self) -> tuple[List[Dict[str, Any]], List[str]]:
        """Panorama por schema + lista completa de nomes físicos do escopo."""
        rows = self.store.execute_read(_DOMAINS_CYPHER, {"scope_id": self.scope_id})
        domains: List[Dict[str, Any]] = []
        catalog: List[str] = []
        for row in rows:
            schema = _schema_short(row.get("schema_fqn"), "")
            names = sorted(n for n in (row.get("names") or []) if n)
            domains.append({
                "schema": schema,
                "domain": _domain_label(schema),
                "table_count": int(row.get("table_count") or len(names)),
                "tables": [f"{schema}.{n}" for n in names],
            })
            catalog.extend(f"{schema}.{n}" for n in names)
        return domains, sorted(set(catalog))

    def _ranked_table_ids(self, seed_ids: List[str]) -> List[tuple[str, int, bool]]:
        """Tabelas ordenadas por relevância: sementes primeiro (na ordem do
        híbrido), depois os vizinhos herdando a posição de quem os puxou.

        Devolve (table_fqn, rank, is_seed) — o `rank` é diagnóstico; a ORDEM da
        lista é o contrato consumido pelo answer builder e pelo eval."""
        seed_rows = self.store.execute_read(_SEED_TABLES_CYPHER, {"ids": seed_ids})
        seeds = [(r["tid"], int(r["rank"])) for r in seed_rows]
        seed_set = {tid for tid, _ in seeds}
        ordered: List[tuple[str, int, bool]] = [(tid, rank, True) for tid, rank in seeds]

        if not (self.expand_neighbors and seeds):
            return ordered

        tids = [tid for tid, _ in seeds]
        rows = self.store.execute_read(_NEIGHBORS_CYPHER, {"tids": tids})
        # Vizinho vem SEMPRE depois de toda semente: a semente tem evidência
        # direta da pergunta, o vizinho só evidência de vizinhança. Entre
        # vizinhos, desempata quem foi puxado por mais sementes.
        neighbors = [
            (r["neighbor_id"], int(r["rank"]), int(r.get("links") or 1))
            for r in rows if r["neighbor_id"] not in seed_set
        ]
        neighbors.sort(key=lambda item: (item[1], -item[2], item[0]))
        ordered.extend((nid, rank, False) for nid, rank, _ in neighbors)
        return ordered

    def _select_columns(
        self,
        all_cols: list,
        *,
        table_fqn: str,
        seed_column_fqns: set,
        join_key_names: set,
        query_tokens: set,
        is_seed_table: bool = True,
    ) -> list:
        """Colunas que ajudam a ENTENDER a tabela, **dentro de um orçamento fixo**.

        A seleção é por prioridade, e o teto (`max_columns`) vale para o total —
        inclusive para as essenciais. É o que garante que melhorar a relevância
        não engorde o prompt: as vagas são as mesmas, muda quem as ocupa.

        Ordem: (1) estruturais — PK e chave de JOIN, que sustentam grão e JOIN e
        são poucas; (2) colunas-semente, que a busca vetorial já elegeu; (3) as
        mais relevantes para a pergunta, por `_pontua_colunas`; (4) preenchimento
        por ordinal, que é só contexto de reconhecimento.

        Antes, "essencial" entrava sem teto e o teto só cortava o preenchimento —
        então uma coluna importante nunca era cortada por falta de espaço, e sim
        por não ser RECONHECIDA como importante (poda por substring crua)."""
        orcamento = self.max_columns if is_seed_table else self.max_columns_expansion
        estruturais, sementes = [], []
        for col in all_cols:
            name = col["name"]
            if col.get("is_pk") or name in join_key_names:
                estruturais.append(name)
            elif f"{table_fqn}.{name}" in seed_column_fqns:
                sementes.append(name)

        pontos = _pontua_colunas(all_cols, query_tokens)
        relevantes = [
            nome for nome, _ in sorted(pontos.items(), key=lambda kv: (-kv[1], kv[0]))
            if pontos[nome] > 0
        ]

        keep_names: List[str] = []
        for grupo in (estruturais, relevantes, sementes, [c["name"] for c in all_cols]):
            for nome in grupo:
                if len(keep_names) >= orcamento:
                    break
                if nome not in keep_names:
                    keep_names.append(nome)

        escolhidas = set(keep_names)
        return [c for c in all_cols if c["name"] in escolhidas]

    @staticmethod
    def _neighbor_labels(table_fqn: str, table_joins: List[dict]) -> List[str]:
        """Descrição curta das conexões da tabela, do ponto de vista dela."""
        out: List[str] = []
        for j in table_joins:
            if j["left_table_fqn"] == table_fqn:
                other, mine, theirs = j["right_table"], j["left_col"], j["right_col"]
            else:
                other, mine, theirs = j["left_table"], j["right_col"], j["left_col"]
            direct = "JOIN direto" if j.get("sql_join_eligible") else "requer normalização"
            label = f"{other} ({mine} = {theirs}, {direct})"
            if label not in out:
                out.append(label)
        return out

    @staticmethod
    def _serialize(row: dict, sql_name: str, schema: str, columns: list,
                   neighbors: List[str], omitidas: List[str] | None = None) -> str:
        """Card de catálogo para o LLM: o que a tabela é, em que grão, com quem
        conversa. Mais enxuto que a serialização da Persona 2 (que precisa de
        tipo e descrição de cada coluna para escrever SQL de indicador)."""
        lines = [
            f"Tabela: {sql_name}   (FQN: {row['table_fqn']})",
            f"  Domínio: {_domain_label(schema)}",
        ]
        grain = _grain_for(row["table_fqn"], row.get("table_grain"))
        if grain:
            lines.append(f"  Grão: {grain}")
        if row.get("table_desc"):
            lines.append(f"  Descrição: {row['table_desc']}")
        lines.append(
            "  Conecta-se com: " + ("; ".join(neighbors) if neighbors
                                    else "(nenhuma — tabela isolada no grafo)")
        )
        cols = ", ".join(
            f"{c['name']}{' [PK]' if c.get('is_pk') else ''} {c.get('type', '')}".strip()
            for c in columns
        )
        lines.append(f"  Colunas principais: {cols}")
        # Nunca esconder a EXISTÊNCIA de uma coluna. O detalhe (tipo, descrição)
        # é caro e fica só nas escolhidas; o nome custa ~3 tokens e é o que
        # impede a resposta de afirmar que a tabela não tem algo que ela tem —
        # o modo Explorar fala com quem não pode conferir.
        if omitidas:
            from rag.settings.configs import settings

            teto = settings.EXPLORE_MAX_OMITTED_NAMES
            mostradas = omitidas[:teto] if teto > 0 else []
            if mostradas:
                resto = len(omitidas) - len(mostradas)
                sufixo = f" (+{resto})" if resto > 0 else ""
                lines.append(f"  Outras colunas (só o nome): {', '.join(mostradas)}{sufixo}")
        return "\n".join(lines)

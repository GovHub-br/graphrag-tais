"""
Neo4jSubgraphRetriever — o retriever CANÔNICO do GraphRAG (a "costura").

Recebe os Documents que o QdrantHybridRetriever achou (cada um com `node_id` no
meta) e, a partir desses nós-semente, percorre o grafo por Cypher para montar o
SUBGRAFO DE SCHEMA relevante: as tabelas dos cards, suas colunas e as tabelas
joináveis por `SEMANTIC_JOIN`. Devolve um Document por tabela (schema
serializado) + a lista de JOINs disponíveis, pronto pro PromptBuilder montar o
SQL.

    query → (hybrid Qdrant) → seeds → [ESTE componente] → subgrafo → LLM

Nota de correção (ADR-0011/CLAUDE.md, gap nº1): o MCid **não declara FK**
(FOREIGN_KEY=0, LINEAGE=0). A joinabilidade vem de `SEMANTIC_JOIN` (coluna↔coluna),
extraído das Custom Properties do OpenMetadata. Por isso a travessia é por
`SEMANTIC_JOIN` (+ `HAS_COLUMN`), não por FK — que era o motivo de o passo de
grafo não expandir nada antes.
"""
import re
from typing import Any, Dict, List, Optional

from loguru import logger
from haystack import Document, component

from rag.components.graphstore.neo4j import Neo4jGraphStore

# Resolve os node_ids semente para as TABELAS-semente (sobe de Column para Table).
_SEED_TABLES_CYPHER = """
UNWIND $ids AS nid
MATCH (seed {node_id: nid})
OPTIONAL MATCH (seed)<-[:HAS_COLUMN]-(parent:Table)
WITH collect(DISTINCT CASE WHEN seed:Table THEN seed.node_id END)
   + collect(DISTINCT parent.node_id) AS ids
UNWIND ids AS tid
WITH DISTINCT tid WHERE tid IS NOT NULL
RETURN tid
"""

# Vizinhos de 1 hop por SEMANTIC_JOIN das tabelas-semente, com a elegibilidade do
# join. A decisão de manter ou não o vizinho (modo "evidence") é feita em Python.
_NEIGHBORS_CYPHER = """
UNWIND $tids AS tid
MATCH (t:Table {node_id: tid})-[:HAS_COLUMN]->(:Column)
      -[r:SEMANTIC_JOIN]-(:Column)<-[:HAS_COLUMN]-(nbr:Table)
WHERE nbr.node_id <> tid
RETURN DISTINCT nbr.node_id AS neighbor_id, r.sql_join_eligible AS eligible
"""

# Modos `ppr`/`prize` (expansion.py): arestas tabela↔tabela da bola de 2 hops
# em volta das sementes. O PageRank com α≥0,2 quase não leva massa além disso.
_BALL_EDGES_CYPHER = """
UNWIND $tids AS tid
MATCH (t:Table {node_id: tid})
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(:Column)-[r1:SEMANTIC_JOIN]-(:Column)<-[:HAS_COLUMN]-(n:Table)
WHERE n <> t AND ($all OR r1.sql_join_eligible)
WITH collect(DISTINCT t) + collect(DISTINCT n) AS ball
UNWIND ball AS a
MATCH (a)-[:HAS_COLUMN]->(:Column)-[r:SEMANTIC_JOIN]-(:Column)<-[:HAS_COLUMN]-(b:Table)
WHERE a <> b AND ($all OR r.sql_join_eligible)
RETURN DISTINCT a.node_id AS u, b.node_id AS v
"""

# Grau GLOBAL (tabelas distintas ligadas) e produto de dados de cada tabela.
_TABLE_STATS_CYPHER = """
UNWIND $ids AS id
MATCH (t:Table {node_id: id})
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(:Column)-[r:SEMANTIC_JOIN]-(:Column)<-[:HAS_COLUMN]-(o:Table)
WHERE o <> t AND ($all OR r.sql_join_eligible)
RETURN id, t.data_product AS product, count(DISTINCT o) AS degree
"""

# Colunas (nome, tipo, PK, descrição) das tabelas relevantes, em ordem ordinal.
# Usa `node_id` (não existe prop `fqn` no nó) e `is_primary_key` (não `is_pk`).
_COLUMNS_CYPHER = """
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
ORDER BY table_fqn
"""

# JOINs (SEMANTIC_JOIN) entre pares de tabelas do subgrafo. `sql_join_eligible`
# separa o JOIN direto (join_rule=exact) do que exige normalização documentada
# fora do SQL automático (normalized_exact). `t.node_id < t2.node_id` deduplica
# a aresta não-direcionada.
_JOINS_CYPHER = """
UNWIND $tids AS tid
MATCH (t:Table {node_id: tid})-[:HAS_COLUMN]->(c1:Column)
      -[r:SEMANTIC_JOIN]-(c2:Column)<-[:HAS_COLUMN]-(t2:Table)
WHERE t2.node_id IN $tids AND t.node_id < t2.node_id
RETURN DISTINCT
       t.node_id AS left_table_fqn, t.name AS left_table, c1.name AS left_col,
       t2.node_id AS right_table_fqn, t2.name AS right_table, c2.name AS right_col,
       r.join_rule AS join_rule, r.semantic_key AS semantic_key,
       r.sql_join_eligible AS sql_join_eligible, r.cardinality AS cardinality,
       r.confidence AS confidence
ORDER BY left_table, right_table
"""


_STOPWORDS = {
    "qual", "quais", "quanto", "quantos", "quantas", "como", "onde", "para",
    "por", "com", "dos", "das", "the", "and", "que", "uma", "uns", "umas",
    "todos", "todas", "cada", "entre", "sobre", "mais", "menos", "seu", "sua",
}


def _tokenize(query: str) -> set:
    """Tokens úteis da pergunta (>=4 chars, sem stopwords) para casar colunas."""
    parts = re.split(r"[^0-9a-zA-Zà-úÀ-Ú]+", (query or "").lower())
    return {p for p in parts if len(p) >= 4 and p not in _STOPWORDS}


def _col_matches(name: str, description: str, tokens: set) -> bool:
    """True se algum token da pergunta aparece no nome (por partes) ou descrição."""
    if not tokens:
        return False
    haystack = (name or "").lower().replace("_", " ") + " " + (description or "").lower()
    return any(tok in haystack for tok in tokens)


_GRAIN_CACHE: dict | None = None


def _grain_for(table_fqn: str, node_grain: str | None = None) -> str:
    """Grão declarado da tabela, para o contexto do LLM.

    Duas fontes, nesta ordem:

    1. **propriedade do nó `Table`** — o grão medido que a fonte publica numa
       Custom Property (`OM_TABLE_GRAIN_PROPERTY`; no MinC, `mincTableGrain`,
       presente em 407 das 408 tabelas, contado exato via Trino). É a casa
       definitiva que o MCid ainda não tinha;
    2. **`OM_GRAIN_KEYS`** (FQN -> frase) — a ponte manual, que segue valendo
       para o MCid, onde não há PK, FK nem property e o grão foi medido no
       banco (docs/20260812-metodo-profiling-grao.md).
    """
    if node_grain and node_grain.strip():
        return node_grain.strip()
    global _GRAIN_CACHE
    if _GRAIN_CACHE is None:
        import json

        from rag.settings.configs import settings
        raw = (settings.OM_GRAIN_KEYS or "").strip()
        try:
            _GRAIN_CACHE = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            logger.warning("OM_GRAIN_KEYS não é JSON válido; grão desativado")
            _GRAIN_CACHE = {}
    return _GRAIN_CACHE.get(table_fqn, "")


def _schema_short(schema_fqn: str | None, table_fqn: str) -> str:
    """Último segmento do FQN do schema (ex.: 'Cidades.cidades.conjuntura_gold'
    → 'conjuntura_gold'). É o schema físico usado no SQL (hoje contra o Trino,
    cujo catálogo já é o default da conexão — o SQL não o prefixa)."""
    if schema_fqn:
        return schema_fqn.rsplit(".", 1)[-1]
    # Fallback pelo FQN da tabela: service.database.schema.table
    parts = table_fqn.split(".")
    return parts[-2] if len(parts) >= 2 else ""


def _table_of(doc: Document) -> str:
    """Tabela de um card: o próprio node_id, ou o pai se for card de coluna."""
    meta = doc.meta or {}
    nid = meta.get("node_id") or ""
    if meta.get("card_type") == "table":
        return nid
    return nid.rsplit(".", 1)[0]


def _select_columns_strict(
    all_cols: list,
    *,
    table_fqn: str,
    max_cols: int,
    seed_column_fqns: set,
    join_key_names: set,
    query_tokens: set,
) -> list:
    """Poda com teto ESTRITO (`PRUNE_MODE=strict`, experimental): o teto vale
    para o card inteiro, e a relevância é a da Persona 1 (radical + IDF local,
    sem o ruído de catálogo) em vez da substring crua. Prioridade: PK → chave
    de JOIN → coluna-semente → relevantes → ordinal.

    No modo `legacy`, "essencial" entra sem teto e `_col_matches` casa
    "valor"/"empreendimento" com quase toda coluna — é daí que vêm cards de
    tabela com 60+ colunas."""
    # Import tardio: o módulo da P1 importa deste aqui.
    from rag.components.retrievers.neo4j_catalog_retriever import (
        _pontua_colunas, _radical, _sem_acento, _RUIDO_CATALOGO,
    )

    termos = {
        _radical(_sem_acento(t)) for t in query_tokens
        if _sem_acento(t) not in _RUIDO_CATALOGO
    }
    pontos = _pontua_colunas(all_cols, termos)
    relevantes = [n for n, p in sorted(pontos.items(), key=lambda kv: (-kv[1], kv[0])) if p > 0]
    pks = [c["name"] for c in all_cols if c.get("is_pk")]
    chaves = [c["name"] for c in all_cols if c["name"] in join_key_names]
    sementes = [c["name"] for c in all_cols if f"{table_fqn}.{c['name']}" in seed_column_fqns]

    keep: List[str] = []
    for grupo in (pks, chaves, sementes, relevantes, [c["name"] for c in all_cols]):
        for nome in grupo:
            if len(keep) >= max_cols:
                break
            if nome not in keep:
                keep.append(nome)
    escolhidas = set(keep)
    return [c for c in all_cols if c["name"] in escolhidas]


@component
class Neo4jSubgraphRetriever:
    def __init__(self, graph_store: Neo4jGraphStore, max_seeds: int = 10):
        self.store = graph_store
        self.max_seeds = max_seeds

    @component.output_types(documents=List[Document], joins=List[Dict[str, Any]])
    def run(
        self,
        documents: List[Document],
        query: str = "",
        pool: Optional[List[Document]] = None,
    ) -> dict:
        """`pool` (opcional) é o pool inteiro da busca híbrida; só o modo
        `prize` o usa, como evidência de relevância dos vizinhos."""
        seed_ids: List[str] = []
        seed_column_fqns: set = set()
        seed_table_fqns: set = set()
        for doc in documents or []:
            meta = doc.meta or {}
            nid = meta.get("node_id")
            if not nid:
                continue
            if nid not in seed_ids:
                seed_ids.append(nid)
            card_type = meta.get("card_type")
            if card_type == "table":
                seed_table_fqns.add(nid)
            else:  # coluna (ou desconhecido): sobe pra tabela-pai
                seed_column_fqns.add(nid)
                seed_table_fqns.add(nid.rsplit(".", 1)[0])
        seed_ids = seed_ids[: self.max_seeds]

        if not seed_ids:
            logger.warning("Neo4jSubgraphRetriever: nenhum node_id nos documents-semente")
            return {"documents": [], "joins": []}

        # 1) Tabelas-semente + vizinhos, escolhendo o conjunto conforme o modo.
        from rag.settings.configs import settings
        seed_table_ids = [
            r["tid"] for r in self.store.execute_read(_SEED_TABLES_CYPHER, {"ids": seed_ids})
        ]
        relevant_ids = set(seed_table_ids)
        if seed_table_ids and settings.EXPANSION_MODE in ("ppr", "prize"):
            relevant_ids.update(self._ranked_expansion(seed_table_ids, documents, pool))
        elif seed_table_ids:
            nbr_rows = self.store.execute_read(_NEIGHBORS_CYPHER, {"tids": seed_table_ids})
            seed_set = set(seed_table_ids)
            for nr in nbr_rows:
                nbr = nr["neighbor_id"]
                if settings.EXPANSION_MODE == "evidence":
                    # Cauteloso: mantém o vizinho só se ele também for semente
                    # (evidência dos dois lados) OU o join for elegível (direto).
                    if nr.get("eligible") or nbr in seed_set:
                        relevant_ids.add(nbr)
                else:  # "all": expande todos os vizinhos de 1 hop (comportamento atual)
                    relevant_ids.add(nbr)

        rows = self.store.execute_read(_COLUMNS_CYPHER, {"tids": sorted(relevant_ids)}) if relevant_ids else []
        table_ids = [row["table_fqn"] for row in rows]
        joins = self.store.execute_read(_JOINS_CYPHER, {"tids": table_ids}) if table_ids else []

        # Chaves de JOIN por tabela (nome da coluna) — sempre preservadas na poda.
        join_key_cols: Dict[str, set] = {}
        for j in joins:
            join_key_cols.setdefault(j["left_table_fqn"], set()).add(j["left_col"])
            join_key_cols.setdefault(j["right_table_fqn"], set()).add(j["right_col"])

        query_tokens = _tokenize(query)

        out: List[Document] = []
        total_cols = 0
        for row in rows:
            table_fqn = row["table_fqn"]
            schema = _schema_short(row.get("schema_fqn"), table_fqn)
            sql_name = f"{schema}.{row['table_name']}" if schema else row["table_name"]
            all_cols = [c for c in (row.get("columns") or []) if c and c.get("name")]
            kept = self._select_columns(
                all_cols,
                table_fqn=table_fqn,
                is_seed_table=table_fqn in seed_table_fqns,
                seed_column_fqns=seed_column_fqns,
                join_key_names=join_key_cols.get(table_fqn, set()),
                query_tokens=query_tokens,
            )
            total_cols += len(kept)
            out.append(Document(
                content=self._serialize_table(row, sql_name, kept,
                                              grain=_grain_for(table_fqn,
                                                               row.get("table_grain"))),
                meta={
                    "node_id": table_fqn,
                    "fqn": table_fqn,
                    "label": "Table",
                    "schema": schema,
                    "table": row["table_name"],
                    "sql_name": sql_name,
                    "description": row.get("table_desc") or "",
                    "omitted_column_count": len(all_cols) - len(kept),
                    # Dicionário de dados por coluna (nome, tipo, PK, descrição),
                    # já podado, consumido pelo SqlAnswerBuilder.
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
            "Neo4jSubgraphRetriever: {} seeds → {} tabelas, {} joins, {} colunas serializadas",
            len(seed_ids), len(rows), len(joins), total_cols,
        )
        return {"documents": out, "joins": joins}

    def _ranked_expansion(
        self,
        seed_table_ids: List[str],
        documents: List[Document],
        pool: Optional[List[Document]],
    ) -> List[str]:
        """Vizinhos escolhidos por PageRank personalizado (+ prêmio da busca no
        modo `prize`), até `EXPANSION_BUDGET`. Ver `expansion.py`."""
        from rag.settings.configs import settings
        from rag.components.retrievers.expansion import pool_relevance, select_expansion

        every = not settings.EXPANSION_ELIGIBLE_ONLY
        edges = self.store.execute_read(_BALL_EDGES_CYPHER, {"tids": seed_table_ids, "all": every})
        ball = sorted({e["u"] for e in edges} | {e["v"] for e in edges} | set(seed_table_ids))
        stats = self.store.execute_read(_TABLE_STATS_CYPHER, {"ids": ball, "all": every})
        degree = {r["id"]: int(r["degree"] or 0) for r in stats}
        product = {r["id"]: r.get("product") or "" for r in stats}

        # Massa inicial de cada tabela-semente = melhor score dos cards dela.
        seed_set = set(seed_table_ids)
        weights: Dict[str, float] = {}
        for rank, doc in enumerate(documents or [], start=1):
            table = _table_of(doc)
            if table in seed_set:
                score = doc.score if doc.score is not None else 1.0 / rank
                weights[table] = max(weights.get(table, 0.0), float(score))
        for tid in seed_table_ids:
            weights.setdefault(tid, min(weights.values(), default=1.0))

        relevance = None
        prize_floor = None
        if settings.EXPANSION_MODE == "prize":
            relevance = pool_relevance((_table_of(d), d.score) for d in (pool or documents or []))
            prize_floor = settings.EXPANSION_PRIZE_FLOOR

        chosen = select_expansion(
            weights, edges,
            degree=degree, product=product, relevance=relevance,
            alpha=settings.EXPANSION_PPR_ALPHA,
            degree_beta=settings.EXPANSION_DEGREE_BETA,
            cross_product=settings.EXPANSION_CROSS_PRODUCT,
            budget=settings.EXPANSION_BUDGET,
            min_ratio=settings.EXPANSION_MIN_RATIO,
            prize_floor=prize_floor,
        )
        logger.info("Expansão {}: {} arestas na bola, {} vizinhos escolhidos",
                    settings.EXPANSION_MODE, len(edges), len(chosen))
        return chosen

    def _select_columns(
        self,
        all_cols: list,
        *,
        table_fqn: str,
        is_seed_table: bool,
        seed_column_fqns: set,
        join_key_names: set,
        query_tokens: set,
    ) -> list:
        """Poda de colunas mantendo o essencial: PK + chaves de JOIN + colunas
        que vieram como semente + colunas casadas com a pergunta; completa por
        ordinal até o teto (maior em tabela-semente). Preserva a ordem ordinal."""
        from rag.settings.configs import settings

        max_cols = (
            settings.PRUNE_SEED_TABLE_MAX_COLS if is_seed_table
            else settings.PRUNE_EXPANSION_TABLE_MAX_COLS
        )
        if settings.PRUNE_MODE == "strict":
            return _select_columns_strict(
                all_cols, table_fqn=table_fqn, max_cols=max_cols,
                seed_column_fqns=seed_column_fqns, join_key_names=join_key_names,
                query_tokens=query_tokens,
            )
        keep_names: set = set()
        for col in all_cols:
            name = col["name"]
            col_fqn = f"{table_fqn}.{name}"
            essential = (
                col.get("is_pk")
                or name in join_key_names
                or col_fqn in seed_column_fqns
                or _col_matches(name, col.get("description", ""), query_tokens)
            )
            if essential:
                keep_names.add(name)
        # Completa por ordinal (all_cols já vem ordenado) até o teto.
        for col in all_cols:
            if len(keep_names) >= max_cols:
                break
            keep_names.add(col["name"])
        # Preserva a ordem ordinal original.
        return [c for c in all_cols if c["name"] in keep_names]

    @staticmethod
    def _serialize_table(row: dict, sql_name: str, columns: list,
                         grain: str = "") -> str:
        """DDL-compacto pro contexto do LLM. Mostra o nome físico (schema.table)
        que deve ir no SQL e, entre parênteses, o FQN completo do catálogo.

        O `grain` é o que impede agregação errada: sem ele o gerador soma linhas
        repetidas e infla o resultado. Em 8 tabelas do MCid as linhas estão
        exatamente duplicadas (2x a 8x), e a frase de grão carrega a instrução
        de deduplicar."""
        lines = [f"Tabela: {sql_name}   (FQN: {row['table_fqn']})"]
        # Grão e descrição vêm ANTES das colunas e nessa ordem: são o que
        # distingue tabelas irmãs que falam do mesmo assunto em níveis de
        # agregação diferentes, e é por essa linha que o SYSTEM_PROMPT manda
        # escolher. Enterrá-las depois de 25 colunas era desperdiçar o sinal.
        if grain:
            lines.append(f"  Grão: {grain}")
        if row.get("table_desc"):
            lines.append(f"  Descrição: {row['table_desc']}")
        lines.append("  Colunas:")
        for col in columns:
            pk = " [PK]" if col.get("is_pk") else ""
            desc = f"  — {col['description']}" if col.get("description") else ""
            lines.append(f"    - {col['name']} {col.get('type', '')}{pk}{desc}")
        return "\n".join(lines)

"""Mede o CUSTO do contexto do modo Explorar — antes e depois de mexer na poda.

A poda de colunas existe para segurar prompt e latência (Fase 1/2: p95 de 35k
para 16,8k tokens). Qualquer mudança em QUAIS colunas entram no card precisa
provar que não desfaz isso. Este script mede, para cada pergunta do gold:

- **tamanho do card** (caracteres e estimativa de tokens) — o que vai no prompt;
- **colunas por tabela** — onde o custo mora;
- **latência da recuperação** (embedding + Qdrant + Neo4j), sem LLM;
- **sondagem de cobertura**: as colunas que a pergunta claramente pede estão no
  card? É o contrapeso — encolher o prompt cortando o que importa não é ganho.

Não chama LLM: compara recuperação com recuperação. Rode dentro do container.

    docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \\
        python scripts/measure_explore_context.py --output /app/antes.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Dict, List

from rag.settings.configs import settings
from rag.components.embedders.ollama_text_embedder import OllamaTextEmbedderFactory
from rag.components.embedders.fastembed_sparse_text_embedder import FastembedSparseTextEmbedderFactory
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.retrievers.qdrant_hybrid_retriever import QdrantHybridRetrieverFactory
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.retrievers.neo4j_catalog_retriever import Neo4jCatalogRetriever

DATASET = "eval/datasets/explore/mcid-explore-v0.1.0.json"

# Sondagem de COBERTURA DE COLUNA. Para cada pergunta, os conceitos que ela
# claramente pede. A régua não é "existe no card": é **recall contra o Neo4j** —
# de todas as colunas que a tabela REALMENTE tem sobre aquele conceito, quantas
# sobreviveram à poda. É assim que se enxerga o dano de podar, e é o número que
# não pode piorar quando o prompt encolhe.
SONDAS: Dict[str, List[str]] = {
    "localizacao-censitario": ["municipio", "uf", "latitude", "longitude", "cep", "endereco"],
    "far-mapa-001": ["municipio", "uf", "latitude", "longitude"],
    "serie-temporal-001": ["data", "ano", "mes", "periodo", "competencia", "dt"],
    "conjuntura-renda-001": ["rendimento", "renda", "salario"],
    "far-empreendimento-individual-001": ["situacao", "municipio", "uf", "percentual"],
    "conjuntura-emprego-001": ["ocupados", "emprego", "trabalho", "admissoes", "desligamentos"],
}
# Perguntas extras (fora do gold) que motivaram a investigação.
EXTRAS = [
    {"id": "localizacao-censitario",
     "question": ("Quais dados granulares de localização sobre os empreendimentos eu tenho "
                  "que podem ser cruzados com bases externas que contem setores censitários "
                  "do ibge?")},
]

CYPHER_COLUNAS = """
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
WITH t, c, split(t.schema, '.')[-1] + '.' + t.name AS sql_name
WHERE sql_name IN $tabelas
RETURN sql_name AS tabela, collect(c.name) AS colunas
"""


def _casa(termo: str, nome: str) -> bool:
    """Termo curto casa por igualdade/fronteira (senão `uf` casaria `sufixo`)."""
    nome = nome.lower()
    if len(termo) <= 3:
        return nome == termo or nome.startswith(termo + "_") or nome.endswith("_" + termo)
    return termo in nome


def _recall_de_coluna(store, documentos, termos: List[str]) -> dict:
    """Recall das colunas relevantes: card ÷ catálogo, só nas tabelas do card."""
    no_card = {
        (d.meta or {}).get("sql_name", ""): {c["name"].lower()
                                             for c in ((d.meta or {}).get("columns") or [])}
        for d in documentos
    }
    no_card.pop("", None)
    if not no_card:
        return {"relevantes_catalogo": 0, "relevantes_card": 0, "recall": None, "faltando": []}

    catalogo = {r["tabela"]: r["colunas"]
                for r in store.execute_read(CYPHER_COLUNAS, {"tabelas": list(no_card)})}

    esperadas, presentes, faltando = 0, 0, []
    for tabela, colunas in catalogo.items():
        for coluna in colunas:
            if not any(_casa(t, coluna) for t in termos):
                continue
            esperadas += 1
            if coluna.lower() in no_card.get(tabela, set()):
                presentes += 1
            else:
                faltando.append(f"{tabela}.{coluna}")
    return {
        "relevantes_catalogo": esperadas,
        "relevantes_card": presentes,
        "recall": round(presentes / esperadas, 3) if esperadas else None,
        "faltando": sorted(faltando)[:12],
    }


def _tokens(texto: str) -> int:
    """Estimativa de tokens. Sem tiktoken no container: ~4 chars/token em
    português é a régua usada nos relatórios anteriores do projeto."""
    return round(len(texto) / 4)


def _perguntas() -> List[dict]:
    with open(DATASET, encoding="utf-8") as handle:
        casos = json.load(handle)["cases"]
    return [{"id": c["id"], "question": c["question"]} for c in casos] + EXTRAS


def medir() -> dict:
    dense = OllamaTextEmbedderFactory().build()
    sparse = FastembedSparseTextEmbedderFactory().build()
    sparse.warm_up()
    store = QdrantStoreFactory(index=settings.QDRANT_COLLECTION).build()
    hybrid = QdrantHybridRetrieverFactory(store, top_k=settings.EXPLORE_TOP_K).build()
    catalog = Neo4jCatalogRetriever(
        graph_store=Neo4jGraphStoreFactory().build(),
        scope_id=settings.OM_SCOPE_ID if settings.SCOPE_FILTER_ENABLED else None,
        max_seeds=settings.EXPLORE_TOP_K,
        max_tables=settings.EXPLORE_MAX_TABLES,
        max_columns=settings.EXPLORE_MAX_COLUMNS,
        max_columns_expansion=settings.EXPLORE_MAX_COLUMNS_EXPANSION,
        expand_neighbors=settings.EXPLORE_EXPAND_NEIGHBORS,
    )

    resultados = []
    for caso in _perguntas():
        query = caso["question"]
        inicio = time.perf_counter()
        docs = hybrid.run(
            query_embedding=dense.run(text=query)["embedding"],
            query_sparse_embedding=sparse.run(text=query)["sparse_embedding"],
            top_k=settings.EXPLORE_TOP_K,
        )["documents"]
        out = catalog.run(documents=docs, query=query)
        latencia = time.perf_counter() - inicio

        cards = [d.content for d in out["documents"]]
        contexto = "\n\n".join(cards)
        colunas = [len((d.meta or {}).get("columns") or []) for d in out["documents"]]

        cobertura = None
        if caso["id"] in SONDAS:
            cobertura = _recall_de_coluna(catalog.store, out["documents"],
                                          SONDAS[caso["id"]])

        resultados.append({
            "id": caso["id"],
            "tabelas": len(cards),
            "colunas_total": sum(colunas),
            "colunas_por_tabela": round(statistics.mean(colunas), 1) if colunas else 0,
            "chars": len(contexto),
            "tokens_estimados": _tokens(contexto),
            "latencia_s": round(latencia, 2),
            "cobertura": cobertura,
        })
        print(f"  {caso['id']:32} {resultados[-1]['tokens_estimados']:6} tok "
              f"{resultados[-1]['latencia_s']:5.2f}s "
              + ("" if not cobertura else f"  recall_col={cobertura['recall']}"))

    tokens = [r["tokens_estimados"] for r in resultados]
    latencias = [r["latencia_s"] for r in resultados]
    sondas = [r for r in resultados if r["cobertura"] and r["cobertura"]["recall"] is not None]
    resumo = {
        "casos": len(resultados),
        "tokens_media": round(statistics.mean(tokens)),
        "tokens_p95": round(sorted(tokens)[int(len(tokens) * 0.95) - 1]),
        "tokens_max": max(tokens),
        "colunas_total_media": round(statistics.mean(r["colunas_total"] for r in resultados), 1),
        "latencia_media_s": round(statistics.mean(latencias), 2),
        "latencia_p95_s": round(sorted(latencias)[int(len(latencias) * 0.95) - 1], 2),
        "recall_coluna_medio": (round(statistics.mean(r["cobertura"]["recall"] for r in sondas), 3)
                                 if sondas else None),
        "sondas_total": len(sondas),
        "config": {
            "EXPLORE_TOP_K": settings.EXPLORE_TOP_K,
            "EXPLORE_MAX_TABLES": settings.EXPLORE_MAX_TABLES,
            "EXPLORE_MAX_COLUMNS": settings.EXPLORE_MAX_COLUMNS,
            "EXPLORE_MAX_COLUMNS_EXPANSION": settings.EXPLORE_MAX_COLUMNS_EXPANSION,
        },
    }
    return {"resumo": resumo, "casos": resultados}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    dados = medir()
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(dados, handle, ensure_ascii=False, indent=2)
    print("\n== RESUMO ==")
    for chave, valor in dados["resumo"].items():
        print(f"  {chave}: {valor}")


if __name__ == "__main__":
    main()

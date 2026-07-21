"""
Neo4jGraphWriter — componente Haystack custom que escreve nós e arestas no Neo4j
via Cypher em lote (UNWIND + MERGE), idempotente (MERGE não duplica).

Espera as specs produzidas pelo GraphCardBuilder:
  nodes:         [{label, key, props}]
  relationships: [{type, start, end, props}]

Labels e tipos de relação vêm do NOSSO código (não de input do usuário), então
são interpolados na query com segurança após validação básica.
"""
import re
from collections import defaultdict
from typing import Any, Dict, List

from loguru import logger
from haystack import component

from rag.components.graphstore.neo4j import Neo4jGraphStore

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"Identificador Cypher inválido: {name!r}")
    return name


@component
class Neo4jGraphWriter:
    def __init__(self, graph_store: Neo4jGraphStore, batch_size: int = 500):
        self.store = graph_store
        self.batch_size = batch_size

    @component.output_types(nodes_written=int, relationships_written=int)
    def run(self, nodes: List[Dict[str, Any]], relationships: List[Dict[str, Any]]) -> dict:
        n_nodes = self._write_nodes(nodes or [])
        n_rels = self._write_relationships(relationships or [])
        logger.info("Neo4jGraphWriter: %d nós, %d arestas", n_nodes, n_rels)
        return {"nodes_written": n_nodes, "relationships_written": n_rels}

    def _write_nodes(self, nodes: List[Dict[str, Any]]) -> int:
        by_label: Dict[str, list] = defaultdict(list)
        for n in nodes:
            by_label[_safe_ident(n["label"])].append({"key": n["key"], "props": n.get("props", {})})

        total = 0
        for label, rows in by_label.items():
            cypher = (
                f"UNWIND $rows AS row "
                f"MERGE (n:`{label}` {{node_id: row.key}}) "
                f"SET n += row.props"
            )
            for chunk in self._chunks(rows):
                self.store.execute_write(cypher, {"rows": chunk})
                total += len(chunk)
        return total

    def _write_relationships(self, rels: List[Dict[str, Any]]) -> int:
        by_type: Dict[str, list] = defaultdict(list)
        for r in rels:
            by_type[_safe_ident(r["type"])].append(
                {"start": r["start"], "end": r["end"], "props": r.get("props", {})}
            )

        total = 0
        for rtype, rows in by_type.items():
            # MATCH pelos nós já existentes (por node_id) e MERGE a relação.
            cypher = (
                f"UNWIND $rows AS row "
                f"MATCH (a {{node_id: row.start}}) "
                f"MATCH (b {{node_id: row.end}}) "
                f"MERGE (a)-[rel:`{rtype}`]->(b) "
                f"SET rel += row.props"
            )
            for chunk in self._chunks(rows):
                self.store.execute_write(cypher, {"rows": chunk})
                total += len(chunk)
        return total

    def _chunks(self, rows: list):
        for i in range(0, len(rows), self.batch_size):
            yield rows[i:i + self.batch_size]

"""
GraphCardBuilder — componente Haystack custom que converte entidades cruas do
OpenMetadata em:

  - documents:      1 Document por card pesquisável (Table/Column/Metric/Term).
                    content = descrição em linguagem natural; meta = {node_id,
                    label, fqn}. Vai para os embedders → Qdrant.
  - nodes:          [{label, key, props}]        → Neo4jGraphWriter
  - relationships:  [{type, start, end, props}]  → Neo4jGraphWriter

Convenção: `node_id` = fullyQualifiedName (fqn) da entidade — estável e legível,
e é a chave que costura Qdrant (payload) ↔ Neo4j (nós).
"""
from typing import Any, Dict, List

from haystack import Document, component


@component
class GraphCardBuilder:

    @component.output_types(
        documents=List[Document],
        nodes=List[Dict[str, Any]],
        relationships=List[Dict[str, Any]],
    )
    def run(self, entities: Dict[str, Any]) -> dict:
        docs: List[Document] = []
        nodes: List[Dict[str, Any]] = []
        rels: List[Dict[str, Any]] = []

        for table in entities.get("tables", []):
            self._handle_table(table, docs, nodes, rels)
        for metric in entities.get("metrics", []):
            self._handle_metric(metric, docs, nodes)
        for term in entities.get("glossary", []):
            self._handle_term(term, docs, nodes)
        for lineage_doc in entities.get("lineage", []):
            self._handle_lineage(lineage_doc, rels)

        return {"documents": docs, "nodes": nodes, "relationships": rels}

    # ------------------------------------------------------------------ #
    # Tabelas + Colunas + FK + hierarquia Schema/Database
    # ------------------------------------------------------------------ #
    def _handle_table(self, table: dict, docs, nodes, rels) -> None:
        t_fqn = table.get("fullyQualifiedName") or table.get("name")
        if not t_fqn:
            return
        t_desc = table.get("description") or ""
        schema = table.get("databaseSchema") or {}
        database = table.get("database") or {}
        schema_fqn = schema.get("fullyQualifiedName")
        db_fqn = database.get("fullyQualifiedName")
        cols = table.get("columns", [])

        nodes.append({
            "label": "Table",
            "key": t_fqn,
            "props": {"name": table.get("name"), "fqn": t_fqn,
                      "description": t_desc, "schema": schema_fqn, "database": db_fqn},
        })
        col_names = ", ".join(c.get("name", "") for c in cols)
        docs.append(Document(
            content=f"Tabela {t_fqn}. {t_desc} Schema: {schema_fqn}. Colunas: {col_names}.",
            meta={"node_id": t_fqn, "label": "Table", "fqn": t_fqn},
        ))

        # Hierarquia Database → Schema → Table
        if db_fqn:
            nodes.append({"label": "Database", "key": db_fqn,
                          "props": {"name": database.get("name"), "fqn": db_fqn}})
        if schema_fqn:
            nodes.append({"label": "Schema", "key": schema_fqn,
                          "props": {"name": schema.get("name"), "fqn": schema_fqn}})
            rels.append({"type": "IN_SCHEMA", "start": t_fqn, "end": schema_fqn, "props": {}})
            if db_fqn:
                rels.append({"type": "IN_DATABASE", "start": schema_fqn, "end": db_fqn, "props": {}})

        # Colunas
        for col in cols:
            c_fqn = col.get("fullyQualifiedName") or f"{t_fqn}.{col.get('name')}"
            c_desc = col.get("description") or ""
            c_type = col.get("dataType") or col.get("dataTypeDisplay") or ""
            nodes.append({
                "label": "Column",
                "key": c_fqn,
                "props": {"name": col.get("name"), "fqn": c_fqn, "data_type": c_type,
                          "description": c_desc, "is_pk": self._is_pk(col), "table": t_fqn},
            })
            rels.append({"type": "HAS_COLUMN", "start": t_fqn, "end": c_fqn, "props": {}})
            docs.append(Document(
                content=f"Coluna {c_fqn} ({c_type}) da tabela {t_fqn}. {c_desc}",
                meta={"node_id": c_fqn, "label": "Column", "fqn": c_fqn},
            ))

            # Glossary terms via tags de origem "Glossary" (best-effort)
            for tag in col.get("tags", []) or []:
                if (tag.get("source") or "").lower() == "glossary":
                    term_fqn = tag.get("tagFQN")
                    if term_fqn:
                        rels.append({"type": "HAS_TERM", "start": c_fqn, "end": term_fqn, "props": {}})

        # Foreign keys (tableConstraints)
        for constraint in table.get("tableConstraints", []) or []:
            if (constraint.get("constraintType") or "").upper() != "FOREIGN_KEY":
                continue
            local_cols = constraint.get("columns", [])
            referred = constraint.get("referredColumns", [])
            for local, ref in zip(local_cols, referred):
                local_fqn = local if "." in local else f"{t_fqn}.{local}"
                rels.append({"type": "FOREIGN_KEY", "start": local_fqn, "end": ref, "props": {}})

    def _handle_metric(self, metric: dict, docs, nodes) -> None:
        m_fqn = metric.get("fullyQualifiedName") or metric.get("name")
        if not m_fqn:
            return
        m_desc = metric.get("description") or ""
        expression = ((metric.get("metricExpression") or {}).get("code")
                      or metric.get("expression") or "")
        nodes.append({"label": "MetricFlow", "key": m_fqn,
                      "props": {"name": metric.get("name"), "fqn": m_fqn,
                                "description": m_desc, "expression": expression}})
        docs.append(Document(
            content=f"Métrica {m_fqn}. {m_desc} Expressão: {expression}",
            meta={"node_id": m_fqn, "label": "MetricFlow", "fqn": m_fqn},
        ))
        # TODO: ligar MetricFlow-[:USES]->Column/Table quando a fonte expuser as
        #       colunas/tabelas consumidas pela métrica.

    def _handle_term(self, term: dict, docs, nodes) -> None:
        g_fqn = term.get("fullyQualifiedName") or term.get("name")
        if not g_fqn:
            return
        g_desc = term.get("description") or ""
        nodes.append({"label": "GlossaryTerm", "key": g_fqn,
                      "props": {"name": term.get("name"), "fqn": g_fqn, "description": g_desc}})
        docs.append(Document(
            content=f"Termo de negócio {g_fqn}. {g_desc}",
            meta={"node_id": g_fqn, "label": "GlossaryTerm", "fqn": g_fqn},
        ))

    def _handle_lineage(self, lineage_doc: dict, rels) -> None:
        """Converte a resposta de lineage (nodes + up/downstream edges) em arestas Table-LINEAGE->Table."""
        id_to_fqn = {n.get("id"): n.get("fullyQualifiedName") for n in lineage_doc.get("nodes", [])}
        entity = lineage_doc.get("entity") or {}
        if entity.get("id") and entity.get("fullyQualifiedName"):
            id_to_fqn[entity["id"]] = entity["fullyQualifiedName"]
        for edge in (lineage_doc.get("upstreamEdges", []) + lineage_doc.get("downstreamEdges", [])):
            src = id_to_fqn.get(edge.get("fromEntity"))
            dst = id_to_fqn.get(edge.get("toEntity"))
            if src and dst:
                rels.append({"type": "LINEAGE", "start": src, "end": dst, "props": {}})

    @staticmethod
    def _is_pk(col: dict) -> bool:
        constraint = (col.get("constraint") or "").upper()
        return constraint in {"PRIMARY_KEY", "PK"}

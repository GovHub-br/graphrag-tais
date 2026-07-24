"""Endpoint Hayhooks acionado pelo Airflow para sincronizar os metadados."""

from __future__ import annotations

from hayhooks import BasePipelineWrapper
from loguru import logger

from rag.pipelines.ingest.ingest_pipeline import build_ingest_pipeline


class PipelineWrapper(BasePipelineWrapper):
    """Wrapper operacional: o escopo vem somente das variáveis de ambiente."""

    skip_mcp = True

    def setup(self) -> None:
        self.pipeline = build_ingest_pipeline()
        logger.info("Wrapper de ingestão OpenMetadata -> Neo4j/Qdrant inicializado")

    def run_api(self) -> dict:
        """Executa uma sincronização completa do escopo configurado."""
        if not self.pipeline:
            raise RuntimeError("Pipeline de ingestão não inicializado")

        # Exceções sobem para o Hayhooks. Assim o Airflow recebe HTTP não-2xx e
        # não registra falso sucesso com {"status": "erro"} em HTTP 200.
        result = self.pipeline.run(
            {},
            include_outputs_from=[
                "om_source",
                "record_builder",
                "card_builder",
                "neo4j_writer",
            ],
        )
        snapshot = result["om_source"]["snapshot"]
        records = result["record_builder"]["records"]
        card_summary = result["card_builder"]["summary"]
        graph_summary = result["neo4j_writer"]
        qdrant_summary = result["qdrant_writer"]

        response = {
            "status": "completed",
            "scope_id": records["scope_id"],
            "ingestion_run_id": records["ingestion_run_id"],
            "source_version": records["source_version"],
            "tables": card_summary["tables"],
            "columns": card_summary["columns"],
            "relationships": card_summary["relationships"],
            "relationships_by_type": records["stats"]["relationships_by_type"],
            "cards": card_summary["cards"],
            "nodes_written": graph_summary["nodes_written"],
            "relationships_written": graph_summary["relationships_written"],
            "documents_written": qdrant_summary["documents_written"],
            "tables_rejected": snapshot["stats"]["tables_rejected"],
            "tables_rejected_by_reason": snapshot["stats"][
                "tables_rejected_by_reason"
            ],
        }
        logger.info("Ingestão concluída: {}", response)
        return response

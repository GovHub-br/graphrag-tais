"""
Wrapper de API (Hayhooks) para a ingestão do grafo de metadados.

Dispara a sincronização: puxa do OpenMetadata, escreve o grafo no Neo4j e os
cards (com embedding híbrido) no Qdrant. Sem corpo obrigatório.
"""
import sys

from hayhooks import BasePipelineWrapper

from rag.pipelines.ingest.ingest_pipeline import build_ingest_pipeline


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        print(">>> Wrapper de Ingestão (OpenMetadata → grafo + Qdrant) INICIALIZADO <<<")

    def run_api(self, collection: str | None = None, with_lineage: bool = True) -> dict:
        print(f">>> [ingest] sync OpenMetadata | collection={collection} | lineage={with_lineage}")
        try:
            pipeline = build_ingest_pipeline(collection=collection, with_lineage=with_lineage)
            result = pipeline.run({})
            return {
                "status": "sucesso",
                "nos_escritos": result.get("neo4j_writer", {}).get("nodes_written", 0),
                "arestas_escritas": result.get("neo4j_writer", {}).get("relationships_written", 0),
                "cards_indexados": result.get("qdrant_writer", {}).get("documents_written", 0),
            }
        except Exception as e:
            print(f"!!! [ingest] ERRO: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return {"status": "erro", "mensagem": str(e)}

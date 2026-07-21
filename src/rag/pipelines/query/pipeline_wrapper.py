"""
Wrapper de API (Hayhooks) para a query (GraphRAG de metadados → SQL).

Espera um JSON: {"query": "pergunta em linguagem natural..."}
"""
import sys

from hayhooks import BasePipelineWrapper

from rag.pipelines.query.query_pipeline import build_query_pipeline


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        self.pipeline = build_query_pipeline()
        print(">>> Wrapper de Query (GraphRAG → SQL) INICIALIZADO <<<")

    def run_api(self, query: str, top_k: int = 5) -> dict:
        print(f">>> [query] '{query[:60]}...' (top_k={top_k})")
        try:
            if not self.pipeline:
                raise RuntimeError("Pipeline não inicializado no setup.")

            result = self.pipeline.run(
                {
                    "dense_text_embedder": {"text": query},
                    "sparse_text_embedder": {"text": query},
                    "hybrid_retriever": {"top_k": top_k},
                    "prompt_builder": {"query": query},
                },
                include_outputs_from=["subgraph_retriever"],
            )

            sql = result["llm"]["replies"][0]
            tabelas = result.get("subgraph_retriever", {}).get("documents", [])

            return {
                "status": "sucesso",
                "sql": sql,
                "tabelas_no_contexto": [d.meta.get("fqn") for d in tabelas],
            }
        except Exception as e:
            print(f"!!! [query] ERRO: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return {"status": "erro", "mensagem": str(e)}

"""
Pipeline de ENTREGA (ADR-0010/0013) — separada do motor, consome sua saída.

    SQL (do motor) ─► mcp_executor (Agent + postgres-mcp, autocorreção)
                          │ linhas + colunas
                          ├─► chart_spec (LLM escolhe o gráfico)
                          │        │ spec
                          └────────┴─► chart_renderer ─► PNG (data URI)

O motor (recuperação + geração de SQL) permanece inalterado e reusável; esta
pipeline executa o SQL contra o banco (read-only, via MCP) e produz o gráfico.
Tolerante a falha: se a execução ou o gráfico falharem, os componentes devolvem
vazio e a resposta segue só com o texto do motor.
"""
from haystack import Pipeline

from rag.components.executors.mcp_sql_executor import McpSqlExecutor
from rag.components.charts.chart_spec import ChartSpecBuilder
from rag.components.charts.chart_renderer import ChartRenderer


def build_delivery_pipeline() -> Pipeline:
    pipeline = Pipeline()

    pipeline.add_component("mcp_executor", McpSqlExecutor())
    pipeline.add_component("chart_spec", ChartSpecBuilder())
    pipeline.add_component("chart_renderer", ChartRenderer())

    # execução → spec do gráfico
    pipeline.connect("mcp_executor.rows", "chart_spec.rows")
    pipeline.connect("mcp_executor.columns", "chart_spec.columns")
    # execução + spec → render
    pipeline.connect("mcp_executor.rows", "chart_renderer.rows")
    pipeline.connect("chart_spec.spec", "chart_renderer.spec")

    return pipeline

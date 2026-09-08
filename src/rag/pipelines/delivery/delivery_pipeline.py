"""
Pipeline de ENTREGA (ADR-0010/0013) — separada do motor, consome sua saída.

    SQL (do motor) ─► mcp_executor (MCP de SQL; direto e, no erro, Agent)
                          │ linhas + colunas
                          ├─► chart_spec (LLM escolhe o gráfico)
                          │        │ spec
                          ├────────┴─► chart_renderer ─► PNG (data URI)
                          │
                          └─► result_explainer ─► parágrafo sobre o resultado

O motor (recuperação + geração de SQL) permanece inalterado e reusável; esta
pipeline executa o SQL contra o banco (read-only, via MCP — trino-mcp por
default desde o ADR-0017, backend configurável) e produz o gráfico e
a explicação do resultado (itens b e c do ADR-0010). Tolerante a falha: se a
execução, o gráfico ou a explicação falharem, os componentes devolvem vazio e a
resposta segue só com o texto do motor.
"""
from haystack import Pipeline

from rag.components.executors.mcp_sql_executor import McpSqlExecutor
from rag.components.charts.chart_spec import ChartSpecBuilder
from rag.components.charts.chart_renderer import ChartRenderer
from rag.components.answer.result_explainer import ResultExplainer


def build_delivery_pipeline() -> Pipeline:
    pipeline = Pipeline()

    pipeline.add_component("mcp_executor", McpSqlExecutor())
    pipeline.add_component("chart_spec", ChartSpecBuilder())
    pipeline.add_component("chart_renderer", ChartRenderer())
    pipeline.add_component("result_explainer", ResultExplainer())

    # execução → spec do gráfico
    pipeline.connect("mcp_executor.rows", "chart_spec.rows")
    pipeline.connect("mcp_executor.columns", "chart_spec.columns")
    # execução + spec → render
    pipeline.connect("mcp_executor.rows", "chart_renderer.rows")
    pipeline.connect("chart_spec.spec", "chart_renderer.spec")
    # execução → explicação do resultado. `exec_ok` vai junto porque linhas
    # vazias são ambíguas: consulta válida sem resultado e execução recusada
    # chegam aqui iguais, e só a primeira merece uma frase.
    pipeline.connect("mcp_executor.rows", "result_explainer.rows")
    pipeline.connect("mcp_executor.columns", "result_explainer.columns")
    pipeline.connect("mcp_executor.exec_ok", "result_explainer.exec_ok")

    return pipeline

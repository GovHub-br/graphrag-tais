"""ResultExplainer — o item (c) do ADR-0010: explicação do resultado EXECUTADO.

A resposta da Persona 2 já trazia a explicação do *caminho* (que tabela, por que
ela, qual SQL) e o gráfico. Faltava a frase sobre o **dado que voltou**: o gráfico
chegava sozinho, sem ninguém dizer o que ele mostra. Este componente escreve esse
parágrafo — curto, colado no gráfico.

**Não recebe as linhas cruas para narrar.** O LLM lê um `digest` calculado aqui em
Python (contagem, soma/mín/máx por métrica, maiores e menores categorias, primeiro
e último período) — decisão do ADR-0010: resumo estatístico em vez de linhas
preserva LGPD, gasta poucos tokens e ainda ancora o texto em número real. A regra
de ouro do prompt é a contrapartida disso: só pode citar número que está no digest.

Aditivo e tolerante a falha, como o gráfico: sem execução, sem OpenAI ou com
qualquer erro, devolve string vazia e a resposta segue sem a seção.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from haystack import component
from haystack.dataclasses import ChatMessage

from rag import prompts
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
# Fonte da verdade única da tipagem de coluna: a mesma classificação que decide o
# gráfico decide o que é métrica aqui — texto e imagem não podem discordar.
from rag.components.charts.chart_spec import ChartSpecBuilder, _infer_kinds
from rag.settings.configs import settings

_MAX_NUMERIC_COLUMNS = 3   # colunas numéricas detalhadas no digest
_SEM_LINHAS = (
    "A consulta rodou no banco, mas não retornou nenhuma linha para os "
    "filtros aplicados."
)


@component
class ResultExplainer:
    def __init__(self, top_n: int | None = None, max_tokens: int | None = None):
        self.top_n = top_n or settings.RESULT_SUMMARY_TOP_N
        self.max_tokens = max_tokens or settings.RESULT_SUMMARY_MAX_TOKENS

    @component.output_types(summary=str)
    def run(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        columns: Optional[List[str]] = None,
        exec_ok: bool = False,
        query: str = "",
        ir: Optional[Dict[str, Any]] = None,
    ) -> dict:
        if not settings.RESULT_SUMMARY_ENABLED:
            return {"summary": ""}
        # Sem execução não há resultado para explicar — e a falha não vira texto
        # na resposta (mesma decisão de produto do bloco de SQL).
        if not exec_ok:
            return {"summary": ""}

        rows = rows or []
        columns = columns or list(rows[0].keys() if rows else [])
        if not rows:
            return {"summary": _SEM_LINHAS}

        digest = build_digest(rows, columns, top_n=self.top_n)
        try:
            summary = self._ask_llm(query, (ir or {}).get("intent", "?"), digest)
        except Exception as exc:  # noqa: BLE001 — explicação é aditiva
            logger.warning("ResultExplainer: LLM falhou ({}); sem explicação", exc)
            return {"summary": ""}

        logger.info("ResultExplainer: {} caracteres sobre {} linhas",
                    len(summary), len(rows))
        return {"summary": summary}

    # ------------------------------------------------------------------ #
    def _ask_llm(self, query: str, intent: str, digest: str) -> str:
        gen = OpenAIChatGeneratorFactory(json_mode=False,
                                         max_tokens=self.max_tokens).build()
        reply = gen.run(messages=[
            ChatMessage.from_system(prompts.load("result_summary_system")),
            ChatMessage.from_user(prompts.render(
                "result_summary_user", query=query, intent=intent or "?", digest=digest,
            )),
        ])
        return _one_paragraph(reply["replies"][0].text)


# --------------------------------------------------------------------------- #
# Digest — o que o LLM enxerta do resultado (nunca as linhas cruas)
# --------------------------------------------------------------------------- #
def build_digest(rows: List[Dict[str, Any]], columns: List[str],
                 top_n: int = 5) -> str:
    """Resumo textual e determinístico do resultado executado.

    Função de módulo (e não método) para ser testável sem LLM: o que este texto
    contém é o teto do que a explicação pode afirmar."""
    kinds = _infer_kinds(columns, rows)
    numeric = [c for c in columns if kinds.get(c) == "numeric"]
    rest = [c for c in columns if c not in numeric]
    temporal = [c for c in rest if _is_temporal(c, kinds)]
    categoric = [c for c in rest if c not in temporal]

    linhas = [f"Linhas retornadas: {len(rows)}"]
    if len(rows) >= settings.MAX_EXEC_ROWS:
        linhas.append(
            f"⚠️ o resultado bateu no limite de {settings.MAX_EXEC_ROWS} linhas "
            "da execução — a leitura é parcial."
        )
    linhas.append("Colunas: " + ", ".join(
        f"{c} ({_KIND_PT.get(kinds.get(c, 'text'), 'texto')})" for c in columns
    ))

    # Resultado escalar (um número): o digest É o valor.
    if len(rows) == 1:
        linhas.append("Valores: " + " · ".join(
            f"{c}: {_fmt(rows[0].get(c))}" for c in columns
        ))
        return "\n".join(linhas)

    for col in numeric[:_MAX_NUMERIC_COLUMNS]:
        valores = [v for v in (_as_float(r.get(col)) for r in rows) if v is not None]
        if not valores:
            continue
        linhas.append(
            f"{col} — soma: {_fmt(sum(valores))} · média: "
            f"{_fmt(sum(valores) / len(valores))} · mínimo: {_fmt(min(valores))} "
            f"· máximo: {_fmt(max(valores))}"
        )

    metrica = numeric[0] if numeric else None
    rotulo = (categoric or temporal or [None])[0]
    if metrica and rotulo:
        ordenadas = sorted(
            (r for r in rows if _as_float(r.get(metrica)) is not None),
            key=lambda r: _as_float(r.get(metrica)),
            reverse=True,
        )
        if ordenadas:
            linhas.append(
                f"Maiores {metrica} por {rotulo}: "
                + " · ".join(f"{r.get(rotulo)}: {_fmt(r.get(metrica))}"
                             for r in ordenadas[:top_n])
            )
            if len(ordenadas) > top_n:
                linhas.append(
                    "Menores: " + " · ".join(
                        f"{r.get(rotulo)}: {_fmt(r.get(metrica))}"
                        for r in ordenadas[-2:]
                    )
                )

    # Série temporal: a ordem das linhas é a do SQL, então primeiro/último são o
    # começo e o fim da série — é o que dá "subiu" ou "caiu" sem inventar cálculo.
    if metrica and temporal:
        eixo = temporal[0]
        linhas.append(
            f"Série por {eixo}: começa em {rows[0].get(eixo)} com "
            f"{_fmt(rows[0].get(metrica))} e termina em {rows[-1].get(eixo)} com "
            f"{_fmt(rows[-1].get(metrica))}"
        )
    return "\n".join(linhas)


_KIND_PT = {"numeric": "número", "datetime": "data", "text": "texto"}


def _is_temporal(col: str, kinds: Dict[str, str]) -> bool:
    return kinds.get(col) == "datetime" or any(
        tok in col.lower() for tok in ChartSpecBuilder._TEMPORAL
    )


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    """Número no formato brasileiro (1.234,56); o resto sai como veio."""
    number = _as_float(value)
    if number is None:
        return "—" if value is None else str(value)
    inteiro = abs(number - round(number)) < 1e-9
    texto = f"{number:,.0f}" if inteiro else f"{number:,.2f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _one_paragraph(text: str) -> str:
    """Um parágrafo só: o modelo às vezes acrescenta contexto num segundo bloco."""
    limpo = (text or "").strip()
    return limpo.split("\n\n")[0].strip() if limpo else ""

"""SqlAnswerBuilder — formaliza a saída do motor numa resposta legível.

Consome a resposta ESTRUTURADA do LLM (JSON: explicação + SQL + IR + tabelas
usadas) e o subgrafo recuperado, e produz dois artefatos:

- `answer`  : dict estruturado (para a API `/query/run`);
- `markdown`: texto organizado para o OpenWebUI (explicação, SQL, referências e,
  quando a entrega executou o SQL, o parágrafo "Resultado" — ADR-0010 item c).

O Markdown é montado por `render_markdown(answer, exec_info)`, função de módulo
(não método) porque o wrapper do chat a chama **de novo** depois da camada de
entrega, agora sabendo o que aconteceu na execução — é assim que a resposta
mostra o SQL que RODOU, e não a primeira tentativa do LLM.

Entrega o item (a) do ADR-0010 (bases/tabelas/colunas + SQL). A IR fica no
`answer` como ponto de controle inspecionável (ADR-0006/0007), mas **não é
renderizada**: intent/metric/filters é vocabulário de engenharia, e quem pergunta
não faz nada com ele.

As **referências são ancoradas no subgrafo recuperado** (meta dos Documents), não
extraídas do texto do LLM — o que a `tables_used` do LLM afirma é apenas usado
para *marcar* o que foi efetivamente utilizado.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger
from haystack import Document, component
from pydantic import BaseModel, Field, ValidationError


# --------------------------------------------------------------------------- #
# Contrato da saída do LLM (IR mínima do ADR-0006/0007 + entregáveis do usuário)
# --------------------------------------------------------------------------- #
class IRFilter(BaseModel):
    concept: str = ""
    value: Any = None


class QuestionIR(BaseModel):
    """IR mínima (ADR-0007 §3): intent, metric, filters — em conceitos de negócio."""
    intent: str = "unknown"
    metric: Optional[str] = None
    filters: List[IRFilter] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    explanation: str = ""
    sql: str = ""
    ir: QuestionIR = Field(default_factory=QuestionIR)
    tables_used: List[str] = Field(default_factory=list)


def _reply_text(reply: Any) -> str:
    """Extrai o texto de um reply (ChatMessage do Haystack 3 ou str)."""
    if reply is None:
        return ""
    for attr in ("text", "content"):
        value = getattr(reply, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(reply)


def _parse_answer(text: str) -> GeneratedAnswer:
    """Parse tolerante do JSON do LLM. Em falha, degrada para explicação=texto."""
    try:
        payload = json.loads(text)
        return GeneratedAnswer.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("SqlAnswerBuilder: resposta não-JSON do LLM ({}); usando fallback", exc)
        return GeneratedAnswer(explanation=text.strip())


@component
class SqlAnswerBuilder:

    @component.output_types(answer=Dict[str, Any], markdown=str)
    def run(
        self,
        replies: List[Any],
        documents: Optional[List[Document]] = None,
        joins: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        documents = documents or []
        joins = joins or []
        text = _reply_text(replies[0]) if replies else ""
        parsed = _parse_answer(text)

        references = self._build_references(documents, parsed.tables_used, joins)
        answer = {
            "status": "sucesso",
            "explanation": parsed.explanation,
            "sql": parsed.sql,
            "ir": parsed.ir.model_dump(),
            "references": references,
            "context_tables": [d.meta.get("sql_name") for d in documents],
            "joins": joins,
        }
        markdown = render_markdown(answer)
        return {"answer": answer, "markdown": markdown}

    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_references(
        documents: List[Document],
        tables_used: List[str],
        joins: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Referências ancoradas no subgrafo. Marca as que o LLM diz ter usado;
        se nada casar, considera todas as tabelas do contexto como referência.
        Anota também quais colunas são chave de JOIN (SEMANTIC_JOIN)."""
        used = {t.strip().lower() for t in tables_used if t}
        # (nome curto da tabela, coluna) que participam de algum JOIN
        join_keys = set()
        for j in joins:
            join_keys.add((j.get("left_table", ""), j.get("left_col", "")))
            join_keys.add((j.get("right_table", ""), j.get("right_col", "")))

        refs: List[Dict[str, Any]] = []
        for doc in documents:
            meta = doc.meta or {}
            sql_name = meta.get("sql_name") or meta.get("table") or ""
            short = meta.get("table", "")
            is_used = sql_name.lower() in used or short.lower() in used
            columns = [
                {**col, "is_join_key": (short, col.get("name")) in join_keys}
                for col in meta.get("columns", [])
            ]
            refs.append({
                "base": meta.get("schema", ""),
                "table": sql_name,
                "fqn": meta.get("fqn", ""),
                "description": meta.get("description", ""),
                "columns": columns,
                "used": is_used,
            })
        if any(r["used"] for r in refs):
            return [r for r in refs if r["used"]]
        return refs


# --------------------------------------------------------------------------- #
# Renderização em Markdown — a partir do `answer` ESTRUTURADO
#
# Mora fora da classe porque roda DUAS vezes por resposta do chat: aqui, dentro
# da pipeline (antes de existir execução), e de novo no wrapper, depois que a
# camada de entrega executou o SQL. Renderizar dos CAMPOS (nunca reparseando o
# Markdown já montado) é a mesma regra que o relatório .docx segue.
# --------------------------------------------------------------------------- #
# Cap de colunas quando não dá pra saber quais foram usadas (ex.: SELECT *).
MAX_DICT_COLUMNS = 15


def _column_keys(col: Dict[str, Any]) -> str:
    """Coluna 'Chave' do dicionário: PK e/ou chave de JOIN (SEMANTIC_JOIN)."""
    marks = []
    if col.get("is_pk"):
        marks.append("🔑 PK")
    if col.get("is_join_key"):
        marks.append("🔗 join")
    return " · ".join(marks)


def _sql_identifiers(sql: str) -> set:
    """Identificadores (nomes) citados no SQL, em minúsculas."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", (sql or "").lower()))


def _sql_section(sql: str, exec_info: Dict[str, Any]) -> tuple[str, str]:
    """Bloco do SQL + o SQL que vale como fonte da verdade para o dicionário.

    **Invariante: aparece UM SQL só — o que RODOU.** Quando a execução ajustou a
    consulta (autocorreção do `McpSqlExecutor`), é a versão ajustada que aparece;
    a tentativa que o banco recusou não interessa a quem lê e não é mostrada.

    Quando a execução falhou de vez, mostra-se o **SQL gerado**, limpo: sem erro
    de banco e sem aviso. O usuário quer a consulta para levar embora, não o
    diagnóstico da falha — que continua no log do `McpSqlExecutor` e no
    `delivery` (portanto no `.docx` e no que a instrumentação registrar)."""
    sql = (sql or "").strip()
    executed = (exec_info.get("sql_final") or "").strip()

    if exec_info.get("exec_ok") and (executed or sql):
        final = executed or sql
        block = "### SQL executado\n```sql\n" + final + "\n```"
        if executed and sql and executed != sql:
            block += "\n\n> O SQL foi **ajustado na execução** para rodar."
        return block, final

    if sql:
        return "### SQL gerado\n```sql\n" + sql + "\n```", sql

    return (
        "### SQL gerado\n_Não foi possível gerar SQL com o schema "
        "recuperado. Refine a pergunta ou verifique se as tabelas "
        "necessárias foram ingeridas._",
        "",
    )


def _render_data_dictionary(references: List[Dict[str, Any]], sql: str) -> str:
    """Uma tabela Markdown por tabela referenciada: nome em destaque
    (cabeçalho) + linhas de coluna com tipo/chave. Mostra APENAS as colunas
    efetivamente citadas no SQL, para não poluir com dezenas de colunas."""
    tokens = _sql_identifiers(sql)
    # `SELECT *` / `SELECT t.*` (mas não `COUNT(*)`) → não dá pra saber as
    # colunas; mostra amostra curta em vez de só a chave de JOIN.
    select_star = bool(re.search(r"select\s+(\w+\.)?\*", (sql or "").lower()))
    blocks = ["### Dados de referência"]
    by_base: Dict[str, List[Dict[str, Any]]] = {}
    for ref in references:
        by_base.setdefault(ref["base"] or "—", []).append(ref)

    for base, refs in sorted(by_base.items()):
        blocks.append(f"**Base `{base}`**")
        for ref in refs:
            columns = ref["columns"]
            # Só as colunas usadas no SQL, para não poluir.
            shown = [c for c in columns if c.get("name", "").lower() in tokens]
            overflow = 0
            if select_star or not shown:
                shown = columns[:MAX_DICT_COLUMNS]
                overflow = max(0, len(columns) - MAX_DICT_COLUMNS)

            if ref["description"]:
                blocks.append(f"_{ref['description'].strip()}_")
            # Cabeçalho = nome da tabela (linha em destaque); colunas abaixo.
            lines = [
                f"| 📋 `{ref['table']}` | Tipo · Chave |",
                "| :-- | :-- |",
            ]
            for col in shown:
                props = col.get("type", "") or ""
                keys = _column_keys(col)
                if keys:
                    props = f"{props} · {keys}" if props else keys
                lines.append(f"| `{col.get('name', '')}` | {props} |")
            if overflow:
                lines.append(f"| _… +{overflow} colunas_ | |")
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_markdown(answer: Dict[str, Any],
                    exec_info: Dict[str, Any] | None = None) -> str:
    """Markdown do chat a partir do `answer` estruturado.

    `exec_info` é o que a camada de entrega apurou
    (`{sql_final, exec_ok, exec_error, result_summary}`). Sem ele, a resposta é a
    do motor puro — nenhuma afirmação sobre execução é feita, e a seção
    "Resultado" (que só existe se o SQL rodou) não aparece."""
    exec_info = exec_info or {}
    references: List[Dict[str, Any]] = answer.get("references") or []
    joins: List[Dict[str, Any]] = answer.get("joins") or []
    parts: List[str] = []

    # 1) Explicação em linguagem natural
    parts.append((answer.get("explanation") or "").strip() or "_(sem explicação)_")

    # 2) SQL — o que rodou, ou o gerado marcado como não executado
    sql_block, sql_efetivo = _sql_section(answer.get("sql", ""), exec_info)
    parts.append(sql_block)

    # 3) Dados de referência — dicionário por tabela (ancorado no subgrafo),
    #    derivado do SQL EFETIVO: se a execução corrigiu uma coluna, é a coluna
    #    corrigida que precisa ser documentada.
    if references:
        if any(r["used"] for r in references):
            parts.append(_render_data_dictionary(references, sql_efetivo))
        else:
            # Fallback (LLM não indicou tabelas): lista compacta do contexto,
            # sem estourar a resposta com dicionários de dezenas de tabelas.
            names = ", ".join(f"`{r['table']}`" for r in references)
            parts.append("### Tabelas disponíveis no contexto\n" + names)

    # 4) JOINs disponíveis no contexto (SEMANTIC_JOIN). Só o ⚠️ nas que não
    # casam direto; a legenda abaixo explica o que ele significa.
    if joins:
        join_lines = ["### Relações de JOIN no contexto"]
        needs_norm = False
        for j in joins:
            left = f"{j['left_table']}.{j['left_col']}"
            right = f"{j['right_table']}.{j['right_col']}"
            mark = "" if j.get("sql_join_eligible") else " ⚠️"
            if not j.get("sql_join_eligible"):
                needs_norm = True
            join_lines.append(f"- `{left}` = `{right}`{mark}")
        if needs_norm:
            join_lines.append(
                "\n> ⚠️ a chave não casa direto — exige normalização "
                "(formato/limpeza) antes do JOIN."
            )
        parts.append("\n".join(join_lines))

    # 5) Resultado — o parágrafo que diz o que o dado executado mostra
    # (ADR-0010 item c). Fica por ÚLTIMO de propósito: o wrapper anexa o gráfico
    # logo depois, e é esse texto que serve de legenda para ele.
    resumo = (exec_info.get("result_summary") or "").strip()
    if resumo:
        parts.append("### Resultado\n\n" + resumo)

    # A IR (intent/metric/filters) NÃO é renderizada: ela é ponto de controle de
    # engenharia (ADR-0006/0007) e continua no `answer` da API `/query/run` e no
    # log — mas, no chat e no relatório, era jargão sem uso para quem pergunta.
    return "\n\n".join(parts)

"""SqlAnswerBuilder — formaliza a saída do motor numa resposta legível.

Consome a resposta ESTRUTURADA do LLM (JSON: explicação + SQL + IR + tabelas
usadas) e o subgrafo recuperado, e produz dois artefatos:

- `answer`  : dict estruturado (para a API `/query/run`);
- `markdown`: texto organizado para o OpenWebUI (explicação, SQL, referências).

Entrega o item (a) do ADR-0010 (bases/tabelas/colunas + SQL) e expõe a IR como
ponto de controle inspecionável (ADR-0006/0007). As **referências são ancoradas
no subgrafo recuperado** (meta dos Documents), não extraídas do texto do LLM —
o que a `tables_used` do LLM afirma é apenas usado para *marcar* o que foi
efetivamente utilizado.
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
        markdown = self._render_markdown(parsed, references, joins)
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

    # ------------------------------------------------------------------ #
    # Cap de colunas quando não dá pra saber quais foram usadas (ex.: SELECT *).
    MAX_DICT_COLUMNS = 15

    @staticmethod
    def _column_keys(col: Dict[str, Any]) -> str:
        """Coluna 'Chave' do dicionário: PK e/ou chave de JOIN (SEMANTIC_JOIN)."""
        marks = []
        if col.get("is_pk"):
            marks.append("🔑 PK")
        if col.get("is_join_key"):
            marks.append("🔗 join")
        return " · ".join(marks)

    @staticmethod
    def _sql_identifiers(sql: str) -> set:
        """Identificadores (nomes) citados no SQL, em minúsculas."""
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", (sql or "").lower()))

    def _render_data_dictionary(
        self, references: List[Dict[str, Any]], sql: str
    ) -> str:
        """Uma tabela Markdown por tabela referenciada: nome em destaque
        (cabeçalho) + linhas de coluna com tipo/chave. Mostra APENAS as colunas
        efetivamente citadas no SQL, para não poluir com dezenas de colunas."""
        tokens = self._sql_identifiers(sql)
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
                    shown = columns[: self.MAX_DICT_COLUMNS]
                    overflow = max(0, len(columns) - self.MAX_DICT_COLUMNS)

                if ref["description"]:
                    blocks.append(f"_{ref['description'].strip()}_")
                # Cabeçalho = nome da tabela (linha em destaque); colunas abaixo.
                lines = [
                    f"| 📋 `{ref['table']}` | Tipo · Chave |",
                    "| :-- | :-- |",
                ]
                for col in shown:
                    props = col.get("type", "") or ""
                    keys = self._column_keys(col)
                    if keys:
                        props = f"{props} · {keys}" if props else keys
                    lines.append(f"| `{col.get('name', '')}` | {props} |")
                if overflow:
                    lines.append(f"| _… +{overflow} colunas_ | |")
                blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ #
    def _render_markdown(
        self,
        parsed: GeneratedAnswer,
        references: List[Dict[str, Any]],
        joins: List[Dict[str, Any]],
    ) -> str:
        parts: List[str] = []

        # 1) Explicação em linguagem natural
        parts.append(parsed.explanation.strip() or "_(sem explicação)_")

        # 2) SQL gerado
        if parsed.sql.strip():
            parts.append("### SQL gerado\n```sql\n" + parsed.sql.strip() + "\n```")
        else:
            parts.append(
                "### SQL gerado\n_Não foi possível gerar SQL com o schema "
                "recuperado. Refine a pergunta ou verifique se as tabelas "
                "necessárias foram ingeridas._"
            )

        # 3) Dados de referência — dicionário de dados por tabela (ancorado no subgrafo)
        if references:
            any_used = any(r["used"] for r in references)
            if any_used:
                parts.append(self._render_data_dictionary(references, parsed.sql))
            else:
                # Fallback (LLM não indicou tabelas): lista compacta do contexto,
                # sem estourar a resposta com dicionários de dezenas de tabelas.
                names = ", ".join(f"`{r['table']}`" for r in references)
                parts.append(
                    "### Tabelas disponíveis no contexto\n" + names
                )

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

        # 5) Interpretação (IR) — ponto de controle inspecionável (ADR-0006/0007).
        # Markdown puro (o <details> antigo quebrava no render do OpenWebUI).
        ir = parsed.ir
        ir_bits = [f"intent `{ir.intent}`"]
        if ir.metric:
            ir_bits.append(f"metric `{ir.metric}`")
        if ir.filters:
            filtros = "; ".join(
                f"{f.concept}={f.value}" for f in ir.filters if f.concept
            )
            if filtros:
                ir_bits.append(f"filtros: {filtros}")
        parts.append("**Interpretação (IR):** " + " · ".join(ir_bits))

        return "\n\n".join(parts)

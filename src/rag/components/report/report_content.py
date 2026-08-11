"""Renderização ESTRUTURADA do relatório (ADR-0010, opção D).

Monta o corpo do `.docx` diretamente do `answer` do motor (explicação, SQL,
`references`, `ir`, `joins`) + o PNG do gráfico + info da execução — **sem parsear
Markdown**. Isso elimina a classe de bugs do conversor de Markdown (crases/underscores
crus) e dá controle total da formatação de cada seção.

O `answer` vem do `SqlAnswerBuilder`:
  {status, explanation, sql, ir:{intent,metric,filters:[{concept,value}]},
   references:[{base,table,fqn,description,columns:[{name,type,is_pk,is_join_key}],used}],
   joins:[{left_table,left_col,right_table,right_col,sql_join_eligible}]}
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from docx.document import Document as _Document

from rag.components.report import docx_writer as w
from rag.components.report import theme

_MAX_DICT_COLUMNS = 15


def render_answer(doc: _Document, answer: Dict[str, Any],
                  chart_data_uri: str = "", exec_info: Dict[str, Any] | None = None) -> None:
    exec_info = exec_info or {}
    explanation = (answer.get("explanation") or "").strip()
    sql = (answer.get("sql") or "").strip()
    references: List[dict] = answer.get("references") or []
    joins: List[dict] = answer.get("joins") or []
    ir: Dict[str, Any] = answer.get("ir") or {}

    # 1) Explicação
    if explanation:
        w.add_body_text(doc, explanation)
    else:
        w.add_muted(doc, "(sem explicação)")

    # 2) SQL gerado (+ SQL ajustado na execução, se houver)
    w.add_heading(doc, "SQL gerado", 3)
    if sql:
        w.add_code_box(doc, sql)
    else:
        w.add_muted(
            doc,
            "Não foi possível gerar SQL com o schema recuperado. Refine a "
            "pergunta ou verifique se as tabelas necessárias foram ingeridas.",
        )
    sql_final = (exec_info.get("sql_final") or "").strip()
    if exec_info.get("exec_ok") and sql_final and sql_final != sql:
        w.add_muted(doc, "SQL ajustado na execução para rodar:", italic=False)
        w.add_code_box(doc, sql_final)

    # 3) Dados de referência (dicionário por tabela, ancorado no subgrafo)
    if references:
        used = [r for r in references if r.get("used")] or references
        w.add_heading(doc, "Dados de referência", 3)
        tokens = _sql_identifiers(sql)
        select_star = bool(re.search(r"select\s+(\w+\.)?\*", sql.lower()))
        by_base: Dict[str, List[dict]] = {}
        for ref in used:
            by_base.setdefault(ref.get("base") or "—", []).append(ref)
        for base, refs in sorted(by_base.items()):
            p = doc.add_paragraph()
            r = p.add_run("Base ")
            r.bold = True
            theme.set_run_font(r, theme.FONT_BASE)
            w.add_mono_chip(p, base, bold=True)
            for ref in refs:
                desc = (ref.get("description") or "").strip()
                if desc:
                    w.add_muted(doc, desc)
                cols = _select_columns(ref.get("columns") or [], tokens, select_star)
                w.add_dictionary_table(doc, ref.get("table", ""), cols)

    # 4) Relações de JOIN no contexto
    if joins:
        w.add_heading(doc, "Relações de JOIN no contexto", 3)
        needs_norm = False
        for j in joins:
            p = _bullet(doc)
            w.add_mono_chip(p, f"{j.get('left_table','')}.{j.get('left_col','')}")
            sep = p.add_run(" = ")
            sep.font.color.rgb = theme.TEXT_BODY
            theme.set_run_font(sep, theme.FONT_BASE)
            w.add_mono_chip(p, f"{j.get('right_table','')}.{j.get('right_col','')}")
            if not j.get("sql_join_eligible"):
                needs_norm = True
                p.add_run(" ⚠️")
        if needs_norm:
            w.add_callout(
                doc,
                text="⚠️ a chave não casa direto: exige normalização "
                "(formato/limpeza) antes do JOIN.",
            )

    # 5) Interpretação (IR)
    _render_ir(doc, ir)

    # 6) Gráfico (+ nota de execução, se falhou)
    if chart_data_uri:
        w.add_heading(doc, "Gráfico", 3)
        if not w.add_chart(doc, chart_data_uri) and exec_info.get("exec_error"):
            w.add_callout(doc, text=f"Não consegui gerar o gráfico: {exec_info['exec_error']}")
    elif not exec_info.get("exec_ok") and exec_info.get("exec_error"):
        w.add_callout(
            doc,
            text="Não consegui executar o SQL para gerar o gráfico "
            f"({exec_info['exec_error']}).",
        )


# --------------------------------------------------------------------------- #
def _render_ir(doc: _Document, ir: Dict[str, Any]) -> None:
    p = doc.add_paragraph()
    lbl = p.add_run("Interpretação (IR): ")
    lbl.bold = True
    theme.set_run_font(lbl, theme.FONT_BASE)

    def _txt(t: str):
        r = p.add_run(t)
        r.font.color.rgb = theme.TEXT_BODY
        theme.set_run_font(r, theme.FONT_BASE)

    _txt("intent ")
    w.add_mono_chip(p, ir.get("intent") or "unknown")
    if ir.get("metric"):
        _txt(" · metric ")
        w.add_mono_chip(p, str(ir.get("metric")))
    filters = ir.get("filters") or []
    fstr = "; ".join(
        f"{f.get('concept')}={f.get('value')}" for f in filters if f.get("concept")
    )
    if fstr:
        _txt(" · filtros: " + fstr)


def _bullet(doc: _Document):
    try:
        return doc.add_paragraph(style="List Bullet")
    except KeyError:
        p = doc.add_paragraph()
        p.add_run("• ")
        return p


def _sql_identifiers(sql: str) -> set:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", (sql or "").lower()))


def _select_columns(columns: List[dict], tokens: set, select_star: bool) -> List[dict]:
    """Mesma lógica do chat: mostra só as colunas citadas no SQL; se `SELECT *`
    ou nenhuma casar, mostra uma amostra curta."""
    shown = [c for c in columns if (c.get("name", "") or "").lower() in tokens]
    if select_star or not shown:
        shown = columns[:_MAX_DICT_COLUMNS]
    return shown

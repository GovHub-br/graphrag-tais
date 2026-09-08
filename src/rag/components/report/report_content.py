"""Renderização ESTRUTURADA do relatório (ADR-0010, opção D).

Monta o corpo do `.docx` diretamente do `answer` do motor (explicação, SQL,
`references`, `joins`) + o PNG do gráfico + o que a execução apurou (`exec_info`,
que traz também o parágrafo "Resultado") — **sem parsear Markdown**. Isso elimina
a classe de bugs do conversor de Markdown (crases/underscores crus) e dá controle
total da formatação de cada seção.

O `answer` vem do `SqlAnswerBuilder`:
  {status, explanation, sql, ir:{...},
   references:[{base,table,fqn,description,columns:[{name,type,is_pk,is_join_key}],used}],
   joins:[{left_table,left_col,right_table,right_col,sql_join_eligible}]}

A `ir` chega no `answer` e é deliberadamente **ignorada** aqui: intent/metric/
filters é instrumentação de engenharia, não conteúdo do documento que a pessoa
leva para a reunião.
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

    # 1) Explicação
    if explanation:
        w.add_body_text(doc, explanation)
    else:
        w.add_muted(doc, "(sem explicação)")

    # 2) SQL — em destaque fica o que RODOU, e só ele (mesma invariante do
    #    chat). Execução falhada não vira ruído no relatório: mostra-se o SQL
    #    gerado, limpo; o erro do banco vive no log e no `exec_info`.
    executed = (exec_info.get("sql_final") or "").strip()
    exec_ok = bool(exec_info.get("exec_ok"))
    sql_efetivo = executed if (exec_ok and executed) else sql

    if exec_ok and (executed or sql):
        w.add_heading(doc, "SQL executado", 3)
        w.add_code_box(doc, sql_efetivo)
        if executed and sql and executed != sql:
            w.add_muted(doc, "O SQL foi ajustado na execução para rodar.")
    elif sql:
        w.add_heading(doc, "SQL gerado", 3)
        w.add_code_box(doc, sql)
    else:
        w.add_heading(doc, "SQL gerado", 3)
        w.add_muted(
            doc,
            "Não foi possível gerar SQL com o schema recuperado. Refine a "
            "pergunta ou verifique se as tabelas necessárias foram ingeridas.",
        )

    # 3) Dados de referência (dicionário por tabela, ancorado no subgrafo)
    if references:
        used = [r for r in references if r.get("used")] or references
        w.add_heading(doc, "Dados de referência", 3)
        # Derivado do SQL EFETIVO: se a execução corrigiu uma coluna, é a
        # coluna corrigida que precisa ser documentada.
        tokens = _sql_identifiers(sql_efetivo)
        select_star = bool(re.search(r"select\s+(\w+\.)?\*", sql_efetivo.lower()))
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

    # 5) Resultado — o parágrafo sobre o dado executado (ADR-0010 item c), logo
    #    acima do gráfico, que é o que ele explica. A IR NÃO entra no relatório:
    #    é ponto de controle de engenharia (ADR-0006/0007), não conteúdo para
    #    quem recebe o documento; segue disponível no `answer` da API.
    resumo = (exec_info.get("result_summary") or "").strip()
    if resumo:
        w.add_heading(doc, "Resultado", 3)
        w.add_body_text(doc, resumo)

    # 6) Gráfico. Sem gráfico, o relatório simplesmente não traz a seção: erro
    #    de execução não vira callout aqui (mesma regra do bloco de SQL — o
    #    diagnóstico fica no log, não no documento que a pessoa leva).
    if chart_data_uri:
        w.add_heading(doc, "Gráfico", 3)
        w.add_chart(doc, chart_data_uri)

    # 7) Mascaramento (ADR-0018). Ao contrário do erro de execução — que fica
    #    no log de propósito —, isto PRECISA aparecer: o documento circula sem
    #    quem o gerou por perto, e um `#a3f9c1d20b` sem explicação vira, na
    #    reunião, "o relatório veio com defeito". Callout, não nota de rodapé.
    aviso = _masking_note(exec_info.get("masked_columns") or [])
    if aviso:
        w.add_callout(doc, aviso)


# --------------------------------------------------------------------------- #
def _masking_note(masked_columns: List[str]) -> str:
    """Mesma mensagem do chat, sem a marcação Markdown (aqui o estilo é do
    callout). Texto único: a limitação é a mesma nos dois lugares."""
    nomes = [c for c in (masked_columns or []) if c]
    if not nomes:
        return ""
    lista = ", ".join(nomes)
    verbo = "contêm dado pessoal e aparecem" if len(nomes) > 1 else "contém dado pessoal e aparece"
    plural = "s" if len(nomes) > 1 else ""
    return (
        f"🔒 {lista} {verbo} pseudonimizada{plural}. O código exibido é estável — "
        "o mesmo valor recebe sempre o mesmo código, então dá para contar, "
        "agrupar e cruzar sem identificar ninguém."
    )


# --------------------------------------------------------------------------- #
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

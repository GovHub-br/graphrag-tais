"""ExploreAnswerBuilder — formaliza a resposta da PERSONA 1 (explorar).

A Persona 2 recebe "a resposta"; a Persona 1 recebe **o mapa do território**:
onde o assunto vive, que tabelas servem, em que grão, como se conectam, o que dá
para perguntar, um SQL de amostra para ver o dado — e, explicitamente, **o que
não existe**.

Duas invariantes que separam este builder de um "peça pro LLM listar as tabelas":

- **Nada fora do catálogo.** A lista de tabelas é ancorada no que o retriever de
  catálogo devolveu; o que o LLM cita e não existe é DESCARTADO e contado em
  `off_catalog_tables`. Na Persona 2 uma tabela inventada quebra na execução do
  SQL; aqui ela passaria por verdade — a pessoa está explorando justamente
  porque não sabe o que existe e não tem como conferir.
- **Cobertura não é limitada pelo LLM.** O LLM prioriza e explica; as tabelas
  recuperadas que ele não citou continuam listadas depois, como "outras
  relacionadas". Recall é a métrica da Persona 1.

`ranked_tables` é a ordem de apresentação e é o que o eval pontua (recall@k,
MRR, nDCG) — ver `eval/explore/`.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional

from haystack import Document, component
from loguru import logger
from pydantic import BaseModel, Field, ValidationError


# --------------------------------------------------------------------------- #
# Contrato da saída do LLM
# --------------------------------------------------------------------------- #
class ExploreTable(BaseModel):
    table: str = ""
    why: str = ""
    questions: List[str] = Field(default_factory=list)


class GeneratedExploration(BaseModel):
    summary: str = ""
    tables: List[ExploreTable] = Field(default_factory=list)
    sample_sql: str = ""
    sample_sql_rationale: str = ""
    gaps: List[str] = Field(default_factory=list)


def _reply_text(reply: Any) -> str:
    if reply is None:
        return ""
    for attr in ("text", "content"):
        value = getattr(reply, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(reply)


def _parse(text: str) -> GeneratedExploration:
    """Parse tolerante. Em falha, degrada para o texto como resumo — a resposta
    ainda sai com o panorama determinístico, que não depende do LLM."""
    try:
        return GeneratedExploration.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("ExploreAnswerBuilder: resposta não-JSON do LLM ({}); fallback", exc)
        return GeneratedExploration(summary=text.strip())


def _fold(text: str) -> str:
    """Minúsculas sem acento — para casar 'execução' com 'execucao'."""
    norm = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


# Perguntas sobre o catálogo COMO UM TODO ("quais bases vocês têm?"). O
# retriever devolve top-k para qualquer texto — inclusive para estas —, então o
# caminho "sem sementes" nunca dispara na prática e a resposta sairia como uma
# AMOSTRA de tabelas apresentada como se fosse o catálogo. Aqui a pergunta é
# reconhecida pelo texto e o panorama completo (determinístico, vindo do Cypher
# agregado) vai para o topo da resposta.
_CATALOG_PATTERNS = (
    "quais bases", "que bases", "quais sao as bases", "quais tabelas",
    "que tabelas", "quais bancos", "quais dados", "que dados existem",
    "quais fontes", "que fontes", "o que voces tem", "o que voce tem",
    "o que vcs tem", "o que tem disponivel", "o que esta disponivel",
    "o que ha disponivel", "catalogo", "quais assuntos", "quais dominios",
    "o que da para analisar", "o que da pra analisar",
)
# Qualificadores que denunciam um TEMA: a pergunta é sobre um assunto, não sobre
# o catálogo. "O que tem disponível **sobre isso**?" não é pergunta de catálogo.
_THEME_QUALIFIERS = (" sobre ", " isso", " desse ", " dessa ", " deste ", " desta ")


def _is_catalog_question(query: str) -> bool:
    folded = _fold(f" {query} ")
    if not any(pattern in folded for pattern in _CATALOG_PATTERNS):
        return False
    return not any(qualifier in folded for qualifier in _THEME_QUALIFIERS)


_GAPS_CACHE: dict | None = None


def _known_gaps(query: str) -> List[str]:
    """Lacunas conhecidas que o texto da pergunta dispara (`SCOPE_GAPS`).

    Quando o conceito está fora da ingestão, o sistema hoje **acerta ao recusar**
    — mas recusa mal ("refine a pergunta"). O mapa transforma a recusa em
    informação: "não tenho execução orçamentária; tenho valor contratado e
    desembolsado". Mesmo padrão do `OM_GRAIN_KEYS`: JSON em env, porque é
    conhecimento de escopo e não de código.
    """
    global _GAPS_CACHE
    if _GAPS_CACHE is None:
        from rag.settings.configs import settings

        raw = (settings.SCOPE_GAPS or "").strip()
        try:
            _GAPS_CACHE = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            logger.warning("SCOPE_GAPS não é JSON válido; lacunas desativadas")
            _GAPS_CACHE = {}
    folded = _fold(query)
    return [msg for concept, msg in _GAPS_CACHE.items() if _fold(concept) in folded]


@component
class ExploreAnswerBuilder:

    def __init__(self, max_questions: int = 3, llm_reorder: bool = True,
                 max_listed_tables: int = 12):
        self.max_questions = max_questions
        # Deixar o LLM promover as tabelas mais pertinentes muda MRR/nDCG (para
        # melhor ou pior). É knob justamente para poder medir os dois lados.
        self.llm_reorder = llm_reorder
        self.max_listed_tables = max_listed_tables

    @component.output_types(answer=Dict[str, Any], markdown=str)
    def run(
        self,
        replies: List[Any],
        documents: Optional[List[Document]] = None,
        joins: Optional[List[Dict[str, Any]]] = None,
        domains: Optional[List[Dict[str, Any]]] = None,
        catalog: Optional[List[str]] = None,
        query: str = "",
    ) -> dict:
        documents = documents or []
        joins = joins or []
        domains = domains or []
        catalog = catalog or []
        parsed = _parse(_reply_text(replies[0]) if replies else "")

        retrieved = {(d.meta or {}).get("sql_name", ""): d for d in documents}
        retrieved.pop("", None)
        catalog_set = {c.lower() for c in catalog}

        cited, off_catalog = self._resolve_cited(parsed.tables, retrieved, catalog_set)
        ordered_names = self._order(cited, list(retrieved))
        tables = [self._table_entry(name, retrieved.get(name), cited.get(name))
                  for name in ordered_names]

        gaps = self._merge_gaps(parsed.gaps, query, catalog_set)
        touched = self._touched_domains(tables, domains)
        catalog_scope = _is_catalog_question(query)

        answer = {
            "status": "sucesso",
            "mode": "explore",
            "query": query,
            "summary": parsed.summary.strip(),
            "domains": touched if tables else domains,
            # Panorama completo do escopo — sempre disponível na resposta
            # estruturada; entra no Markdown quando a pergunta é de catálogo.
            "catalog_domains": domains,
            "catalog_scope": catalog_scope,
            "tables": tables,
            # Ordem de apresentação = ranking avaliado pelo eval da Persona 1.
            "ranked_tables": ordered_names,
            "joins": joins,
            "sample_sql": parsed.sample_sql.strip(),
            "sample_sql_rationale": parsed.sample_sql_rationale.strip(),
            "gaps": gaps,
            "catalog_size": len(catalog),
            # Diagnóstico do eval: tabela citada pelo LLM que não existe.
            "off_catalog_tables": off_catalog,
            "next_step": (
                "Quer que eu **meça** um desses indicadores (com SQL executado e "
                "gráfico)? Troque para o modelo **`Indicador`** e "
                "diga qual tabela ou indicador você quer."
            ),
        }
        return {"answer": answer, "markdown": self._render(answer, joins)}

    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_cited(
        cited: List[ExploreTable],
        retrieved: Dict[str, Document],
        catalog_set: set,
    ) -> tuple[Dict[str, ExploreTable], List[str]]:
        """Casa o que o LLM citou com o catálogo real. O que não casa é
        descartado — e devolvido à parte, porque a taxa disso é métrica."""
        by_lower = {name.lower(): name for name in retrieved}
        resolved: Dict[str, ExploreTable] = {}
        off_catalog: List[str] = []
        for item in cited:
            raw = (item.table or "").strip().strip("`")
            if not raw:
                continue
            key = raw.lower()
            # Aceita o nome curto quando não houver ambiguidade (o LLM às vezes
            # omite o schema); nunca aceita nome que não esteja no catálogo.
            name = by_lower.get(key)
            if not name:
                matches = [n for lower, n in by_lower.items()
                           if lower.rsplit(".", 1)[-1] == key.rsplit(".", 1)[-1]]
                name = matches[0] if len(matches) == 1 else None
            if name:
                resolved.setdefault(name, item)
            elif key not in catalog_set:
                off_catalog.append(raw)
        return resolved, off_catalog

    def _order(self, cited: Dict[str, ExploreTable], retrieved_order: List[str]) -> List[str]:
        """Ordem final: priorizadas pelo LLM primeiro (na ordem dele), depois as
        demais recuperadas na ordem do retrieval (relevância do híbrido)."""
        if not self.llm_reorder:
            return retrieved_order
        rest = [n for n in retrieved_order if n not in cited]
        return list(cited) + rest

    @staticmethod
    def _table_entry(name: str, doc: Document | None, cited: ExploreTable | None) -> dict:
        meta = (doc.meta if doc else {}) or {}
        return {
            "table": name,
            "fqn": meta.get("fqn", ""),
            "domain": meta.get("domain", ""),
            "schema": meta.get("schema", ""),
            "description": meta.get("description", ""),
            "grain": meta.get("grain", ""),
            "is_seed": bool(meta.get("is_seed")),
            "is_island": bool(meta.get("is_island")),
            "neighbors": meta.get("neighbors", []),
            "columns": meta.get("columns", []),
            "why": (cited.why.strip() if cited else ""),
            "questions": [q.strip() for q in (cited.questions if cited else []) if q.strip()],
            "highlighted": cited is not None,
        }

    def _merge_gaps(self, llm_gaps: List[str], query: str, catalog_set: set) -> List[str]:
        """Lacunas determinísticas primeiro; as do LLM só se não nomearem uma
        tabela que existe (senão viram ruído contraditório)."""
        gaps = list(_known_gaps(query))
        for gap in llm_gaps:
            text = (gap or "").strip()
            if not text:
                continue
            if any(name in _fold(text) for name in (_fold(c) for c in catalog_set)):
                continue
            if text not in gaps:
                gaps.append(text)
        return gaps

    @staticmethod
    def _touched_domains(tables: List[dict], domains: List[Dict[str, Any]]) -> List[dict]:
        """Domínios efetivamente tocados, com o total do catálogo por domínio —
        o contraste "3 de 12 tabelas deste domínio" é informação de exploração."""
        counts: Dict[str, int] = {}
        for table in tables:
            schema = table.get("schema") or ""
            counts[schema] = counts.get(schema, 0) + 1
        by_schema = {d["schema"]: d for d in domains}
        out = []
        for schema, hits in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            info = by_schema.get(schema, {})
            out.append({
                "schema": schema,
                "domain": info.get("domain", schema),
                "matched": hits,
                "table_count": info.get("table_count", 0),
            })
        return out

    # ------------------------------------------------------------------ #
    # Renderização
    #
    # A ordem é de leitura, não de completude: quem explora lê o resumo, VARRE a
    # tabela e só desce ao detalhe do que interessou. Por isso o panorama por
    # domínio virou cabeçalho de grupo da própria tabela (era um bloco à parte,
    # que empurrava a informação útil para baixo), e o detalhe longo — descrição
    # inteira e colunas — só sai para as tabelas que o LLM destacou.
    # ------------------------------------------------------------------ #
    MAX_CELL = 120
    # A descrição do catálogo é prosa longa; como texto de apoio ela entra mais
    # curta que a justificativa do LLM, para a linha não virar parágrafo.
    MAX_FALLBACK_CELL = 90

    @classmethod
    def _cell(cls, text: str, limit: int | None = None) -> str:
        """Texto seguro para uma célula Markdown: sem quebra de linha e sem pipe
        (que encerraria a coluna).

        Quando não cabe, procura um FIM DE FRASE e depois um fim de oração
        (vírgula/ponto-e-vírgula) antes de recorrer ao corte por palavra. Frase
        ou oração inteira lê como texto completo; só o último caso ganha "…".
        Reticências toda hora é o que faz a tabela parecer truncada — que era a
        reclamação."""
        clean = " ".join(str(text or "").split()).replace("|", "\\|")
        limit = limit or cls.MAX_CELL
        if len(clean) <= limit:
            return clean

        # Tolerância para FECHAR numa fronteira natural: vale esticar um pouco a
        # célula para a frase terminar inteira em vez de sair com reticências.
        hard = int(limit * 1.25)

        # 1) Primeira frase, se couber e não for curta demais para informar.
        for end in (". ", "; "):
            head = clean.split(end)[0]
            if len(head) <= hard and len(head) >= limit // 3:
                return head.rstrip(" ,;.:")
        # 2) Última oração completa dentro da tolerância.
        window = clean[:hard]
        clause = max(window.rfind(", "), window.rfind("; "))
        if clause >= limit // 3:
            return window[:clause].rstrip(" ,;.:")
        window = clean[:limit]
        # 3) Último recurso: fronteira de palavra, aí sim com reticências.
        space = window.rfind(" ")
        return (window[:space] if space > limit // 2 else window).rstrip(" ,;.:") + "…"

    def _overview_table(self, tables: List[dict], domains: List[dict]) -> str:
        """Tabela-resumo agrupada por domínio: o que dá para varrer com os olhos."""
        shown = tables[: self.max_listed_tables]
        # Coluna de grão só entra se houver grão declarado — coluna vazia é ruído.
        # (No MCid, `OM_GRAIN_KEYS` costuma estar vazio.)
        with_grain = any(t["grain"] for t in shown)
        header = (
            ["| Tabela | Grão | Para que serve |", "| :-- | :-- | :-- |"]
            if with_grain else
            ["| Tabela | Para que serve |", "| :-- | :-- |"]
        )
        totals = {d["schema"]: d.get("table_count", 0) for d in domains}

        lines = ["### Tabelas que podem servir"]
        by_domain: Dict[str, List[dict]] = {}
        for table in shown:
            by_domain.setdefault(table["domain"] or "—", []).append(table)

        for domain, group in by_domain.items():
            total = totals.get(group[0]["schema"], 0)
            count = f"{len(group)} de {total} tabelas" if total else f"{len(group)} tabelas"
            lines.append(f"\n**{domain}** · {count}")
            lines.extend(header)
            for table in group:
                # "Para que serve" prefere a justificativa do LLM (curta, escrita
                # para a pergunta); sem ela, a descrição do catálogo, mais curta
                # ainda — assim toda linha diz alguma coisa e cabe numa linha.
                serve = (
                    self._cell(table["why"]) if table["why"]
                    else self._cell(table["description"] or "—", self.MAX_FALLBACK_CELL)
                )
                if with_grain:
                    lines.append(
                        f"| `{table['table']}` | {self._cell(table['grain'] or '—', 60)} "
                        f"| {serve} |"
                    )
                else:
                    lines.append(f"| `{table['table']}` | {serve} |")

        hidden = tables[self.max_listed_tables:]
        if hidden:
            names = ", ".join(f"`{t['table']}`" for t in hidden)
            lines.append(f"\n_Também relacionadas:_ {names}")
        return "\n".join(lines)

    def _detail_blocks(self, tables: List[dict]) -> str:
        """Detalhe só do que o LLM destacou: descrição completa e colunas."""
        highlighted = [t for t in tables[: self.max_listed_tables] if t["highlighted"]]
        if not highlighted:
            return ""
        lines = ["### Detalhe das principais"]
        for table in highlighted:
            lines.append(f"\n**`{table['table']}`**")
            if table["description"]:
                lines.append(f"_{ ' '.join(table['description'].split()) }_")
            if table["grain"]:
                lines.append(f"- **Grão:** {table['grain']}")
            cols = ", ".join(f"`{c['name']}`" for c in table["columns"][:8])
            if cols:
                lines.append(f"- **Colunas principais:** {cols}")
        return "\n".join(lines)

    @staticmethod
    def _panorama_table(domains: List[dict]) -> str:
        """Panorama do escopo inteiro — determinístico, direto do Cypher."""
        lines = ["### Bases disponíveis", "| Base | Tabelas | Exemplos |",
                 "| :-- | --: | :-- |"]
        for dom in domains:
            examples = ", ".join(
                f"`{name.split('.')[-1]}`" for name in (dom.get("tables") or [])[:3]
            )
            lines.append(
                f"| **{dom['domain']}** | {dom.get('table_count', 0)} | {examples} |"
            )
        return "\n".join(lines)

    def _render(self, answer: dict, joins: List[Dict[str, Any]]) -> str:
        parts: List[str] = [answer["summary"] or "_(sem resumo)_"]
        tables = answer["tables"]
        catalog_domains = answer.get("catalog_domains") or answer["domains"]

        if answer.get("catalog_scope") or not tables:
            # A pergunta é sobre o catálogo inteiro: o panorama vem primeiro e é
            # a resposta. As tabelas recuperadas são só uma amostra do índice
            # vetorial — mostrá-las como "as bases" seria enganoso.
            parts.append(self._panorama_table(catalog_domains))

        if tables:
            overview = self._overview_table(tables, answer["domains"])
            if answer.get("catalog_scope"):
                overview = overview.replace(
                    "### Tabelas que podem servir",
                    "### Uma amostra, pelo que você escreveu",
                    1,
                )
            parts.append(overview)
            detail = self._detail_blocks(tables)
            if detail:
                parts.append(detail)

        # Como se conectam. Dizer que a tabela é ilha é informação, não defeito.
        if tables:
            lines = ["### Como elas se conectam"]
            if joins:
                lines += ["| De | Para | Chave | JOIN |", "| :-- | :-- | :-- | :-- |"]
                for j in joins:
                    mark = "✅ direto" if j.get("sql_join_eligible") else "⚠️ normalizar"
                    lines.append(
                        f"| `{j['left_table']}` | `{j['right_table']}` "
                        f"| `{j['left_col']}` = `{j['right_col']}` | {mark} |"
                    )
                if any(not j.get("sql_join_eligible") for j in joins):
                    lines.append("\n> ⚠️ a chave não casa direto — exige normalização antes do JOIN.")
            islands = [t["table"] for t in tables if t["is_island"]]
            if islands:
                shown = ", ".join(f"`{name}`" for name in islands[:4])
                extra = f" _(+{len(islands) - 4})_" if len(islands) > 4 else ""
                lines.append(
                    f"\n> Sem relação declarada com as demais (uso isolado): {shown}{extra}"
                )
            parts.append("\n".join(lines))

        # O que dá para perguntar — em tabela, para varrer e escolher uma.
        asked = [(t["table"], t["questions"][: self.max_questions])
                 for t in tables if t["questions"]]
        if asked:
            lines = ["### O que dá para perguntar com isso",
                     "| Pergunta | Tabela |", "| :-- | :-- |"]
            for name, questions in asked:
                for question in questions:
                    lines.append(f"| {self._cell(question, 150)} | `{name}` |")
            parts.append("\n".join(lines))

        # 5) SQL de amostra — para ver o dado, não para medir indicador.
        if answer["sample_sql"]:
            block = ["### SQL de amostra (para ver o dado)"]
            if answer["sample_sql_rationale"]:
                block.append(answer["sample_sql_rationale"])
            block.append("```sql\n" + answer["sample_sql"] + "\n```")
            parts.append("\n".join(block))

        # 6) O que NÃO existe.
        if answer["gaps"]:
            lines = ["### O que eu não tenho"]
            lines.extend(f"- {gap}" for gap in answer["gaps"])
            parts.append("\n".join(lines))

        parts.append("---\n\n" + answer["next_step"])
        return "\n\n".join(parts)

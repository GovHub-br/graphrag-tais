"""Roteamento de sementes por produto de dados (experimental, atrás de flag).

Quando vários produtos de dados dividem os mesmos schemas (layout medalhão:
o schema é a camada, não o produto), tabelas de produtos diferentes têm cards
quase iguais. No MCid, FAR, FDS, Rural e Pró-Moradia falam todos de
"empreendimento", "valor contratado" e "UF": a busca híbrida não tem como
saber que a pergunta é do FAR — e devolve sementes de três produtos.

Quando a PERGUNTA cita um produto pelo nome ("FAR", "MCMV Entidades",
"Rural"), os cards desse produto ganham peso ``×(1+β)`` no pool antes do
corte. É uma priorização, não um filtro: se o produto citado não tiver card
bom no pool, os outros continuam lá.

O produto do card vem do payload (`meta.data_product`, gravado pela ingestão
a partir dos Data Products do OpenMetadata). O vocabulário sai do rótulo do
próprio produto (`data_product_label`: o rótulo inteiro, as siglas e, sem as
palavras de `SEED_PRODUCT_LABEL_STOPWORDS`, o resto dele — "Empreendimento
FAR" → "far" com `empreendimento` na lista; "Pró-Moradia" → "pro moradia")
mais o que `SEED_PRODUCT_ALIASES` acrescentar — nome de programa que o rótulo
não contém (ex.: "MCMV Entidades" para o FDS). Nenhum vocabulário de escopo
mora no código.

Medido na bancada de 2026-10-26 (eval/experiments/20261026-*): MRR da P2
0,525 → 0,604; no gold do Explorar, recall@5 de tabela 0,25 → 0,32.
"""
import json
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Set

from haystack import Document, component
from loguru import logger

from rag.settings.configs import settings

def _norm(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", (texto or "").lower())
        if not unicodedata.combining(c)
    )


def _schema_boost(raw: str) -> Dict[str, float]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SEED_SCHEMA_BOOST não é JSON válido; ignorado")
        return {}
    return {str(k): float(v) for k, v in data.items()}


def _stopwords(raw: str) -> tuple[str, ...]:
    return tuple(w for w in (_norm(p).strip() for p in (raw or "").split(",")) if w)


def aliases_from_label(label: str, stopwords: Iterable[str] = ()) -> Set[str]:
    """Formas pelas quais a pergunta cita o produto, derivadas do rótulo.

    ``stopwords`` são palavras que se repetem no início de vários rótulos e
    não identificam nenhum; saem do começo do rótulo (o rótulo inteiro também
    fica, caso a pergunta o cite por extenso)."""
    full = _norm(label).strip()
    base = full
    stripped = True
    while stripped:
        stripped = False
        for word in stopwords:
            if base.startswith(word + " "):
                base = base[len(word) + 1:].lstrip()
                stripped = True
    forms = {full, full.replace("-", " "), base, base.replace("-", " ")}
    forms |= {sigla.lower() for sigla in re.findall(r"\b[A-Z]{3,}\b", label or "")}
    return {f.strip() for f in forms if f.strip()}


def products_in_question(question: str, aliases: Dict[str, Set[str]]) -> Set[str]:
    texto = _norm(question)
    found = set()
    for product, forms in aliases.items():
        for form in forms:
            if re.search(r"(?<![a-z0-9])" + re.escape(form) + r"(?![a-z0-9])", texto):
                found.add(product)
                break
    return found


def _extra_aliases(raw: str) -> Dict[str, Set[str]]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SEED_PRODUCT_ALIASES não é JSON válido; ignorado")
        return {}
    return {str(k): {_norm(a) for a in v} for k, v in data.items() if isinstance(v, list)}


def _schema_of(doc: Document) -> str:
    parts = ((doc.meta or {}).get("node_id") or "").split(".")
    return parts[2] if len(parts) >= 4 else ""


def one_per_table(documents: Iterable[Document]) -> List[Document]:
    """Só o melhor card de cada tabela, na ordem recebida: as k sementes viram
    k tabelas distintas em vez de várias colunas da mesma tabela."""
    seen, out = set(), []
    for doc in documents:
        meta = doc.meta or {}
        table = meta.get("table_node_id") or meta.get("node_id") or doc.id
        if table not in seen:
            seen.add(table)
            out.append(doc)
    return out


def route(
    documents: Iterable[Document],
    question: str,
    *,
    boost: float,
    extra_aliases: Optional[Dict[str, Set[str]]] = None,
    stopwords: Iterable[str] = (),
    schema_boost: Optional[Dict[str, float]] = None,
) -> tuple[List[Document], Set[str]]:
    """Reordena o pool dando ``×(1+boost)`` aos cards do produto citado e
    ``×(1+schema_boost[schema])`` aos cards dos schemas preferidos."""
    docs = list(documents or [])
    aliases: Dict[str, Set[str]] = {}
    for doc in docs:
        meta = doc.meta or {}
        product, label = meta.get("data_product"), meta.get("data_product_label")
        if product and label and product not in aliases:
            aliases[product] = aliases_from_label(label, stopwords)
    for product, forms in (extra_aliases or {}).items():
        aliases.setdefault(product, set()).update(forms)

    found = products_in_question(question, aliases)
    if found:
        for doc in docs:
            if (doc.meta or {}).get("data_product") in found:
                doc.score = (doc.score or 0.0) * (1.0 + boost)
    if schema_boost:
        for doc in docs:
            extra = schema_boost.get(_schema_of(doc))
            if extra:
                doc.score = (doc.score or 0.0) * (1.0 + extra)
    if found or schema_boost:
        docs.sort(key=lambda d: (-(d.score if d.score is not None else 0.0),
                                 (d.meta or {}).get("node_id", d.id or "")))
    return docs, found


@component
class ProductSeedRouter:
    """Prioriza, no pool do híbrido, os cards do produto que a pergunta cita."""

    def __init__(self, top_k: int = 5, boost: float | None = None) -> None:
        self.top_k = top_k
        self.boost = settings.SEED_PRODUCT_BOOST if boost is None else boost
        self.extra = _extra_aliases(settings.SEED_PRODUCT_ALIASES)
        self.stopwords = _stopwords(settings.SEED_PRODUCT_LABEL_STOPWORDS)
        self.schema_boost = _schema_boost(settings.SEED_SCHEMA_BOOST)
        self.distinct_tables = settings.SEED_DISTINCT_TABLES

    @component.output_types(documents=List[Document], pool=List[Document])
    def run(self, documents: List[Document], query: str, top_k: Optional[int] = None) -> dict:
        docs, found = route(documents, query, boost=self.boost,
                            extra_aliases=self.extra, stopwords=self.stopwords,
                            schema_boost=self.schema_boost)
        if found:
            logger.info("Roteamento por produto: {} na pergunta", sorted(found))
        seeds = one_per_table(docs) if self.distinct_tables else docs
        return {"documents": seeds[: (top_k or self.top_k)], "pool": docs}

"""O que o catálogo tem — e não tem — para as trilhas (ADR-0020).

A trilha do especialista em políticas pede a "jornada da política" (lei →
programa → orçamento → execução → resultado) e indicadores complementares (raça,
gênero, emprego). Boa parte disso não existe nas bases ingeridas, e o board da
jornada é explícito: **sem dado, vira lacuna** — o sistema não escreve uma causa
ou um contexto que não consegue mostrar.

Então a resposta não é texto do LLM: é uma varredura determinística do catálogo
do escopo (nomes e descrições de tabela e coluna no Neo4j). Para cada etapa,
três estados:

- `usado`     uma tabela DESTA resposta cobre a etapa;
- `catalogo`  o catálogo tem tabela para a etapa, mas a resposta não a usou;
- `lacuna`    nada no catálogo cobre a etapa.

A varredura casa por **token** (nome partido em `_`, descrição em palavras), não
por substring: "raca" está dentro de "ope*raca*o", e substring transformaria toda
coluna de operação em indicador de raça.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from loguru import logger

from rag.journey.text import fold

#: Etapas da política e os tokens que as denunciam no catálogo.
POLICY_STAGES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Lei", ("lei", "portaria", "decreto", "norma", "normativo", "legislacao", "resolucao")),
    ("Programa", ("programa", "modalidade", "mcmv", "pmcmv", "fnhis", "moradia", "far", "fds")),
    ("Orçamento", ("orcamento", "orcamentaria", "dotacao", "loa", "ogu", "empenho",
                   "empenhado", "previsto", "orcado")),
    ("Execução", ("execucao", "pago", "pagamento", "liquidado", "desembolso", "desembolsado",
                  "liberacao", "contratado", "contratacao", "financeira", "obra", "obras")),
    ("Resultado", ("entrega", "entregue", "entregues", "concluido", "conclusao",
                   "beneficiario", "beneficiarios", "uh", "atendidas", "resultado", "impacto")),
)

#: "Para quem?" — recortes complementares que a política costuma pedir.
COMPLEMENTARY: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Raça/cor", ("raca", "cor", "etnia", "quilombola", "indigena")),
    ("Gênero", ("sexo", "genero", "mulher", "mulheres", "feminino")),
    # "trabalho" fica de fora: "trabalho social" é outra coisa (é etapa do MCMV).
    ("Emprego", ("emprego", "empregos", "postos")),
    ("Renda", ("renda",)),
)


@dataclass
class Item:
    name: str
    tables: List[str] = field(default_factory=list)   # schema.tabela (ou tabela)
    columns: List[str] = field(default_factory=list)  # tabela.coluna

    @property
    def found(self) -> bool:
        return bool(self.tables or self.columns)


@dataclass
class Stage:
    name: str
    state: str          # usado | catalogo | lacuna
    tables: List[str] = field(default_factory=list)


def _tokens(*texts: str) -> Set[str]:
    out: Set[str] = set()
    for texto in texts:
        out.update(fold((texto or "").replace("_", " ")).split())
    return out


# --------------------------------------------------------------------------- #
@dataclass
class CatalogIndex:
    """Nomes e descrições do escopo, já tokenizados. Carregado uma vez."""
    tables: Dict[str, Set[str]] = field(default_factory=dict)          # tabela → tokens
    columns: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)  # (tabela, coluna) → tokens
    #: tabela → rótulo do produto de dados (Data Product do OpenMetadata).
    #: Vazio quando o catálogo não declara produto — aí nada se restringe.
    products: Dict[str, str] = field(default_factory=dict)
    #: tabela → descrição crua do catálogo (o recorte da frase sai daqui).
    descriptions: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, tables: Iterable[dict], columns: Iterable[dict]) -> "CatalogIndex":
        idx = cls()
        for t in tables:
            nome = _qualified(t.get("schema", ""), t.get("name", ""))
            idx.tables[nome] = _tokens(t.get("name", ""), t.get("description", ""))
            if (t.get("product") or "").strip():
                idx.products[nome] = t["product"].strip()
            if (t.get("description") or "").strip():
                idx.descriptions[nome] = t["description"].strip()
        for c in columns:
            tabela = _qualified(c.get("schema", ""), c.get("table", ""))
            idx.columns[(tabela, c.get("name", ""))] = _tokens(c.get("name", ""))
        return idx


def _qualified(schema: str, name: str) -> str:
    curto = (schema or "").split(".")[-1]
    return f"{curto}.{name}" if curto and name else name


def _short(table: str) -> str:
    return table.split(".")[-1]


@lru_cache(maxsize=4)
def load_index(scope_id: str) -> Optional[CatalogIndex]:
    """Lê o catálogo do escopo no Neo4j. None se o grafo estiver fora."""
    try:
        from neo4j import GraphDatabase
        from rag.settings.configs import settings

        driver = GraphDatabase.driver(settings.NEO4J_URL,
                                      auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD))
        with driver.session(database=settings.NEO4J_DATABASE or None) as ses:
            tabelas = ses.run(
                "MATCH (t:Table {scope_id: $s}) RETURN t.schema AS schema, t.name AS name, "
                "t.description AS description, t.data_product_label AS product",
                s=scope_id).data()
            colunas = ses.run(
                "MATCH (t:Table {scope_id: $s})-[:HAS_COLUMN]->(c:Column) "
                "RETURN t.schema AS schema, t.name AS table, c.name AS name", s=scope_id).data()
        driver.close()
        logger.info("[jornada/catalogo] {} tabelas e {} colunas do escopo {}",
                    len(tabelas), len(colunas), scope_id)
        return CatalogIndex.from_rows(tabelas, colunas)
    except Exception as exc:  # noqa: BLE001 — sem catálogo, sem varredura
        logger.warning("[jornada/catalogo] Neo4j indisponível ({})", exc)
        return None


# --------------------------------------------------------------------------- #
def products_of(index: Optional[CatalogIndex], used_tables: Sequence[str]) -> List[str]:
    """Produtos de dados das tabelas usadas, na ordem em que aparecem."""
    if not index:
        return []
    usadas = [_short(t).lower() for t in used_tables if t]
    por_curto = {_short(t).lower(): p for t, p in index.products.items()}
    out: List[str] = []
    for curto in usadas:
        produto = por_curto.get(curto)
        if produto and produto not in out:
            out.append(produto)
    return out


@dataclass
class Scope:
    """De onde é o número, para quem não disse o programa na pergunta."""
    table: str          # schema.tabela
    text: str           # primeira oração da descrição (ou o rótulo do produto)
    forms: Set[str]     # como a pergunta citaria essa base (texto dobrado)


def _first_clause(description: str, limit: int = 90) -> str:
    """'Grandes números do Pró-Moradia por UF: contratos…' → 'Grandes números
    do Pró-Moradia por UF'. Corta em ':', '.', ';' ou ',' e, se ainda for longo,
    na última palavra inteira antes do limite."""
    texto = " ".join((description or "").split())
    for sep in (":", ". ", ";", ","):
        texto = texto.split(sep)[0]
    texto = texto.strip().rstrip(".")
    if len(texto) > limit:
        texto = texto[:limit].rsplit(" ", 1)[0]
    return texto


def scope_of(index: Optional[CatalogIndex], used_tables: Sequence[str]) -> Optional[Scope]:
    """O recorte da primeira tabela usada, com as formas que a denunciam.

    O Data Product não serve sozinho: no MCid as tabelas do Pró-Moradia estão
    no produto "Reloginho MCMV", que junta vários programas. A descrição da
    tabela diz o programa ("Grandes números do Pró-Moradia por UF"); o produto
    fica de reserva para tabela sem descrição."""
    if not index:
        return None
    por_curto = {_short(t).lower(): t for t in index.tables}
    for usada in used_tables:
        chave = por_curto.get(_short(usada).lower())
        if not chave:
            continue
        texto = _first_clause(index.descriptions.get(chave, "")) or index.products.get(chave, "")
        if not texto:
            return None
        partes = [p for p in fold(_short(chave).replace("_", " ")).split() if p]
        # "pro moradia" e "pro-moradia": a pergunta escreve com hífen ou espaço.
        formas = {sep.join(partes[i:i + 2]) for i in range(len(partes) - 1) for sep in (" ", "-")}
        formas |= {s.lower() for s in re.findall(r"\b[A-Z]{3,}\b", texto)}
        return Scope(table=chave, text=texto, forms=formas)
    return None


def policy_stages(index: CatalogIndex, used_tables: Sequence[str]) -> List[Stage]:
    """Etapas da política para ESTA resposta.

    Quando o catálogo declara produto de dados, a varredura fica no produto das
    tabelas usadas. Antes ela varria o catálogo inteiro e, sem tabela usada na
    etapa, listava as três de nome mais curto do escopo: a mesma "Lei" para
    qualquer pergunta, de qualquer programa. Sem produto declarado (outro
    catálogo, ou a resposta sem tabela), vale o catálogo todo, como antes."""
    usadas = {_short(t).lower() for t in used_tables if t}
    produtos = set(products_of(index, used_tables))
    universo = {t: toks for t, toks in index.tables.items()
                if not produtos or index.products.get(t) in produtos}
    etapas = []
    for nome, termos in POLICY_STAGES:
        alvo = set(termos)
        casadas = sorted((t for t, toks in universo.items() if toks & alvo),
                         key=lambda t: (len(t), t))
        nesta = [t for t in casadas if _short(t).lower() in usadas]
        if nesta:
            etapas.append(Stage(nome, "usado", nesta[:3]))
        elif casadas:
            etapas.append(Stage(nome, "catalogo", casadas[:3]))
        else:
            etapas.append(Stage(nome, "lacuna", []))
    return etapas


def complementary(index: CatalogIndex, max_items: int = 4) -> List[Item]:
    itens = []
    for nome, termos in COMPLEMENTARY:
        alvo = set(termos)
        cols = sorted(f"{_short(t)}.{c}" for (t, c), toks in index.columns.items() if toks & alvo)
        tabelas = sorted({c.split(".")[0] for c in cols})
        itens.append(Item(nome, tables=tabelas[:max_items], columns=cols[:max_items]))
    return itens

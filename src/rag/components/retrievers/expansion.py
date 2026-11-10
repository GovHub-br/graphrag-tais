"""Expansão do subgrafo por relevância contínua (experimental, atrás de flag).

O modo `evidence` decide vizinho por vizinho com uma regra binária ("join
elegível ⇒ entra"). Funcionava com 6 arestas; com o catálogo de relações da
skill (~130 arestas, hubs de grau 8–15) vira inundação: toda tabela do mesmo
produto ligada à semente entra, relevante ou não para a pergunta.

Aqui a pergunta muda de "pode entrar?" para "quanto importa, e cabe?":

- **`ppr`** — PageRank personalizado (Haveliwala, 2002; HippoRAG, Gutiérrez et
  al., 2024). Um passeio aleatório que sai das sementes e volta a elas com
  probabilidade α dá a cada tabela a massa ``π = α·s + (1−α)·Wᵀπ``. O peso da
  aresta desconta o hub (``grau(v)^−β``, o IDF do grafo) e o salto entre
  produtos de dados (``γ``). Entram as tabelas de maior π até o orçamento.
- **`prize`** — admissão condicionada à pergunta: ``π(v)·(λ + rel(v))``, onde
  ``rel`` é a relevância que a busca híbrida já deu a algum card de ``v`` (o
  "prêmio" do Prize-Collecting Steiner Tree do G-Retriever, He et al., 2024).
  Vizinho sem evidência na busca só entra se o grafo o puser muito perto.
- Em ambos, a tabela que **liga duas sementes** (vizinha de ≥2 delas) entra
  antes de qualquer outra: é o nó de Steiner de custo mínimo, o que as buscas
  por palavra-chave em banco relacional (BANKS, DISCOVER — 2002) procuram.

Tudo aqui é função pura sobre listas e dicionários: o Cypher fica no retriever.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Set, Tuple

Edge = Tuple[str, str]


def table_adjacency(edges: Iterable[Mapping]) -> Dict[str, Set[str]]:
    """Grafo não-direcionado tabela↔tabela a partir das linhas do Cypher."""
    adj: Dict[str, Set[str]] = {}
    for e in edges:
        u, v = e["u"], e["v"]
        if u == v:
            continue
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    return adj


def transition_weights(
    adj: Mapping[str, Set[str]],
    *,
    degree: Mapping[str, int],
    product: Mapping[str, str],
    degree_beta: float,
    cross_product: float,
) -> Dict[str, Dict[str, float]]:
    """Peso de u→v = grau(v)^−β · (γ se o produto muda).

    O grau usado é o GLOBAL (todas as arestas da tabela no grafo), não o da
    vizinhança recortada — senão um hub na borda do recorte pareceria folha.
    Produto vazio (tabela sem Data Product) não penaliza: falta de rótulo não
    é evidência de produto diferente."""
    weights: Dict[str, Dict[str, float]] = {}
    for u, nbrs in adj.items():
        row: Dict[str, float] = {}
        for v in nbrs:
            w = max(degree.get(v, len(adj.get(v, ()))), 1) ** -degree_beta
            pu, pv = product.get(u) or "", product.get(v) or ""
            if pu and pv and pu != pv:
                w *= cross_product
            if w > 0:
                row[v] = w
        weights[u] = row
    return weights


def personalized_pagerank(
    seeds: Mapping[str, float],
    weights: Mapping[str, Mapping[str, float]],
    *,
    alpha: float,
    iterations: int = 60,
    tol: float = 1e-10,
) -> Dict[str, float]:
    """Iteração de potência de ``π = α·s + (1−α)·Pᵀπ`` (P = pesos normalizados
    por linha). Massa de nó sem saída volta para as sementes, como o salto."""
    total = sum(v for v in seeds.values() if v > 0)
    if total <= 0:
        return {}
    s = {k: v / total for k, v in seeds.items() if v > 0}
    nodes = set(s) | set(weights) | {v for row in weights.values() for v in row}
    out = {u: sum(row.values()) for u, row in weights.items()}
    pi = {n: s.get(n, 0.0) for n in nodes}
    for _ in range(iterations):
        nxt = {n: alpha * s.get(n, 0.0) for n in nodes}
        dangling = 0.0
        for u in nodes:
            mass = pi[u]
            if not mass:
                continue
            row, norm = weights.get(u) or {}, out.get(u, 0.0)
            if norm <= 0:
                dangling += mass
                continue
            share = (1 - alpha) * mass / norm
            for v, w in row.items():
                nxt[v] += share * w
        if dangling:
            for n, val in s.items():
                nxt[n] += (1 - alpha) * dangling * val
        delta = sum(abs(nxt[n] - pi[n]) for n in nodes)
        pi = nxt
        if delta < tol:
            break
    return pi


def connectors(adj: Mapping[str, Set[str]], seeds: Iterable[str]) -> List[str]:
    """Tabelas não-semente vizinhas de duas ou mais sementes (pontes de 2 hops)."""
    seed_set = set(seeds)
    count: Dict[str, int] = {}
    for s in seed_set:
        for v in adj.get(s, ()):
            if v not in seed_set:
                count[v] = count.get(v, 0) + 1
    return sorted((v for v, c in count.items() if c >= 2), key=lambda v: (-count[v], v))


def select_expansion(
    seeds: Mapping[str, float],
    edges: Iterable[Mapping],
    *,
    degree: Mapping[str, int],
    product: Mapping[str, str],
    relevance: Mapping[str, float] | None = None,
    alpha: float = 0.3,
    degree_beta: float = 1.0,
    cross_product: float = 0.2,
    budget: int = 3,
    min_ratio: float = 0.05,
    prize_floor: float | None = None,
) -> List[str]:
    """Tabelas a acrescentar às sementes, em ordem de importância.

    ``min_ratio`` corta pela massa relativa à semente média: abaixo disso o
    vizinho está longe demais para valer o espaço no prompt, mesmo com
    orçamento sobrando. Com ``prize_floor`` (modo `prize`), a nota vira
    ``π·(prize_floor + rel)``."""
    adj = table_adjacency(edges)
    seed_set = set(seeds)
    if not adj or not seed_set:
        return []
    weights = transition_weights(
        adj, degree=degree, product=product,
        degree_beta=degree_beta, cross_product=cross_product,
    )
    pi = personalized_pagerank(seeds, weights, alpha=alpha)
    seed_mass = [pi.get(s, 0.0) for s in seed_set]
    reference = sum(seed_mass) / len(seed_mass) if seed_mass else 0.0

    def score(v: str) -> float:
        base = pi.get(v, 0.0)
        if prize_floor is None:
            return base
        return base * (prize_floor + (relevance or {}).get(v, 0.0))

    chosen: List[str] = []
    for v in connectors(adj, seed_set):
        if len(chosen) >= budget:
            break
        if product.get(v) and all(
            product.get(s) and product.get(s) != product.get(v)
            for s in adj.get(v, ()) if s in seed_set
        ):
            continue  # ponte entre produtos que nenhuma semente compartilha
        chosen.append(v)

    ranked = sorted(
        (v for v in pi if v not in seed_set and v not in chosen),
        key=lambda v: (-score(v), v),
    )
    floor = min_ratio * (reference if prize_floor is None else reference * (prize_floor + 1.0))
    for v in ranked:
        if len(chosen) >= budget:
            break
        if score(v) <= 0 or score(v) < floor:
            break
        chosen.append(v)
    return chosen


def pool_relevance(pool: Iterable[Tuple[str, float]]) -> Dict[str, float]:
    """Relevância por TABELA a partir do pool da busca, recebido como pares
    ``(tabela, score)`` (card de coluna já resolvido para a tabela-pai): o
    melhor score entre os cards dela, normalizado pelo melhor do pool."""
    best: Dict[str, float] = {}
    for node_id, score in pool:
        if not node_id or score is None:
            continue
        best[node_id] = max(best.get(node_id, 0.0), float(score))
    top = max(best.values(), default=0.0)
    if top <= 0:
        return {}
    return {k: v / top for k, v in best.items()}


__all__ = [
    "connectors",
    "personalized_pagerank",
    "pool_relevance",
    "select_expansion",
    "table_adjacency",
    "transition_weights",
]

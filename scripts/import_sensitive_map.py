"""Converte o inventário de PII do dbt no `config/sensitive_columns.yml` (ADR-0018).

O inventário nasce no repositório de dados (`data-application-minc`), a partir
dos `schema.yml` do dbt e da taxonomia do OpenMetadata (`PII.Sensitive`). Ele é
regerado quando o projeto dbt muda — por isso a conversão é um SCRIPT, e não uma
edição manual do YAML: reimportar tem de ser barato, e o diff do arquivo gerado
é o que se revisa no PR.

    python scripts/import_sensitive_map.py \\
        --entrada ~/Downloads/dados-sensiveis-minc.json \\
        --saida config/sensitive_columns.yml

Duas traduções acontecem aqui, e as duas são decisões, não detalhes:

**1. Natureza → estratégia de mascaramento.** O inventário classifica o dado
(`documento`, `bancario`, `credencial`, `raca_etnia`, `deficiencia`); o RAG
precisa saber o que FAZER com ele. Credencial é a única que não se pseudonimiza:
não se conta senha distinta, então `redact`. O resto vira `hash`, que preserva
`COUNT(DISTINCT)`, `GROUP BY` e cruzamento.

**2. Nome do modelo dbt → chave do catálogo.** O inventário nomeia o modelo
(`sac__projetos`), não o `schema.tabela` físico do Trino. O `Catalog` do
ADR-0018 casa nome curto com tabela qualificada, então a chave curta funciona
para qualquer schema — o que é o comportamento desejado enquanto o schema
físico do MinC no `hml_analytics` não estiver confirmado. Conferido no
inventário: dos 56 nomes repetidos (modelo × source), **nenhum** tem conjunto de
colunas divergente, então achatar por nome não perde nem inventa regra.

⚠️ `--excluir-natureza` é onde mora o julgamento. O default exclui `revisar`,
porque é o próprio inventário que diz que aquilo provavelmente não é dado
pessoal. Para tirar também os marcadores de baixa cardinalidade, ver a discussão
em `docs/adr/0018-*.md`:

    --excluir-natureza revisar,raca_etnia,deficiencia
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from typing import Dict, List

#: Natureza (vocabulário do inventário) → estratégia (vocabulário do ADR-0018).
ESTRATEGIA_POR_NATUREZA = {
    "documento": "hash",     # CPF/CNPJ: pseudonimizar preserva contagem distinta
    "bancario": "hash",      # agência/conta: idem
    "raca_etnia": "hash",    # LGPD art. 5º II
    "deficiencia": "hash",   # LGPD art. 5º II
    "credencial": "redact",  # senha/login: não se conta senha distinta
}
#: Naturezas descartadas por default.
#:
#: - `revisar`: instrução do próprio inventário ("marcação existente que
#:   provavelmente não é dado pessoal");
#: - `raca_etnia` / `deficiencia`: decisão de 2026-09-08 (ADR-0018, ação 6).
#:   São LGPD art. 5º II, mas mascarar custaria o recorte de equidade — o
#:   indicador que o sistema existe para produzir — sem ganhar privacidade:
#:   com meia dúzia de valores distintos, o hash cai por análise de
#:   frequência. Quem identifica é o documento, e ele continua mascarado.
EXCLUIR_PADRAO = "revisar,raca_etnia,deficiencia"


def carregar(caminho: str) -> dict:
    with open(caminho, "r", encoding="utf-8") as handle:
        return json.load(handle)


def converter(inventario: dict, excluir: set) -> tuple:
    """Devolve `(tabelas, estatisticas)`. `tabelas` é {nome: {coluna: estrategia}}."""
    tabelas: Dict[str, Dict[str, str]] = defaultdict(dict)
    stats = Counter()
    desconhecidas = Counter()

    for tabela in inventario.get("tabelas", []):
        nome = (tabela.get("tabela") or "").strip().lower()
        if not nome:
            continue
        for coluna in tabela.get("colunas", []):
            natureza = (coluna.get("natureza") or "").strip().lower()
            campo = (coluna.get("coluna") or "").strip().lower()
            if not campo:
                continue
            if natureza in excluir:
                stats["excluidas"] += 1
                continue
            estrategia = ESTRATEGIA_POR_NATUREZA.get(natureza)
            if estrategia is None:
                # Natureza nova no inventário: protege (hash) e AVISA. Silenciar
                # aqui seria deixar dado sensível passar por causa de um rótulo.
                desconhecidas[natureza] += 1
                estrategia = "hash"
            anterior = tabelas[nome].get(campo)
            if anterior and anterior != estrategia:
                # Mesma coluna com naturezas diferentes (modelo × source):
                # `redact` protege mais e ganha.
                estrategia = "redact" if "redact" in (anterior, estrategia) else estrategia
            tabelas[nome][campo] = estrategia
            stats[estrategia] += 1

    return dict(sorted(tabelas.items())), (stats, desconhecidas)


def emitir_yaml(tabelas: dict, inventario: dict, excluir: set, stats: Counter) -> str:
    sobre = inventario.get("_sobre", {})
    linhas: List[str] = [
        "# Catálogo de colunas sensíveis (ADR-0018) — GERADO, não edite à mão.",
        "#",
        "# Fonte: inventário de PII do projeto dbt do MinC",
        f"#   repositório : {sobre.get('repositorio', '?')}",
        f"#   commit      : {sobre.get('commit', '?')}",
        f"#   levantado em: {sobre.get('levantado_em', '?')}",
        f"#   taxonomia   : {sobre.get('taxonomia', '?')}",
        "#",
        "# Regerar após qualquer mudança no inventário:",
        "#   python scripts/import_sensitive_map.py \\",
        "#       --entrada <inventario.json> --saida config/sensitive_columns.yml",
        f"#   (excluídas: {', '.join(sorted(excluir)) or 'nenhuma'})",
        "#",
        f"# Gerado em {date.today().isoformat()}: "
        f"{len(tabelas)} tabelas, {sum(len(v) for v in tabelas.values())} colunas "
        f"({stats['hash']} hash, {stats['redact']} redact, "
        f"{stats['excluidas']} descartadas).",
        "#",
        "# ⚠️ A chave é o nome do MODELO dbt, sem schema. O `Catalog` casa nome",
        "# curto com `schema.tabela` do SQL, então isto vale em qualquer schema —",
        "# de propósito, enquanto o schema físico do MinC no `hml_analytics` não",
        "# estiver confirmado. Fecha para o lado de proteger.",
        "#",
        "# ⚠️ Agregação NUNCA é mascarada: `COUNT(cgccpf)` continua contando",
        "# pessoas. Ver a decisão 4 do ADR-0018.",
        "",
        "version: 1",
        "default_strategy: hash",
        "",
        "tables:",
    ]
    for tabela, colunas in tabelas.items():
        linhas.append(f"  {tabela}:")
        for coluna, estrategia in sorted(colunas.items()):
            if estrategia == "hash":
                linhas.append(f"    - {coluna}")
            else:
                linhas.append(f"    - {{column: {coluna}, strategy: {estrategia}}}")
    linhas += [
        "",
        "# Regra por NOME de coluna, valendo em qualquer tabela. Vazia de",
        "# propósito: cada entrada aqui DESLIGA a autocorreção do SQL por LLM em",
        "# toda consulta (o executor não consegue descartar a hipótese de a",
        "# reescrita tocar a coluna). Use só para identificador que o inventário",
        "# comprovadamente não alcança.",
        "columns: []",
        "",
    ]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--entrada", required=True, help="JSON do inventário de PII")
    parser.add_argument("--saida", default="config/sensitive_columns.yml")
    parser.add_argument(
        "--excluir-natureza", default=EXCLUIR_PADRAO,
        help=f"naturezas descartadas, separadas por vírgula (default: {EXCLUIR_PADRAO})",
    )
    args = parser.parse_args()

    excluir = {n.strip().lower() for n in args.excluir_natureza.split(",") if n.strip()}
    inventario = carregar(args.entrada)
    tabelas, (stats, desconhecidas) = converter(inventario, excluir)

    for natureza, quantidade in desconhecidas.items():
        print(f"⚠️  natureza desconhecida '{natureza}' ({quantidade} colunas) → hash",
              file=sys.stderr)

    with open(args.saida, "w", encoding="utf-8") as handle:
        handle.write(emitir_yaml(tabelas, inventario, excluir, stats))

    print(f"{args.saida}: {len(tabelas)} tabelas, "
          f"{sum(len(v) for v in tabelas.values())} colunas "
          f"({stats['hash']} hash, {stats['redact']} redact, "
          f"{stats['excluidas']} descartadas por natureza).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

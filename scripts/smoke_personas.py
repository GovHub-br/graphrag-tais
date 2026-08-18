#!/usr/bin/env python3
"""Smoke funcional das duas personas contra a API VIVA (ADR-0014).

Não mede qualidade — verifica que o sistema FUNCIONA: os dois modelos existem,
respondem, respeitam o contrato de saída de cada persona e degradam com aviso
quando algo falha. É o complemento vivo dos unitários, que rodam sem serviços.

    python3 scripts/smoke_personas.py [--base http://localhost:8000]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FALHA'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def post(url: str, payload: dict, timeout: int = 240) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def chat(base: str, model: str, text: str) -> tuple[str, float]:
    started = time.time()
    out = post(f"{base}/v1/chat/completions",
               {"model": model, "messages": [{"role": "user", "content": text}]})
    return out["choices"][0]["message"]["content"], time.time() - started


def _display_name(model_id: str, webui: str = "http://localhost:3000") -> str | None:
    """Nome que o open-webui mostra no dropdown para este id.

    Vive no banco do open-webui, não no repo — `scripts/setup_webui_models.sh`
    reaplica. Devolve None se o open-webui não estiver de pé (o smoke do
    Hayhooks não depende dele)."""
    try:
        token = post(f"{webui}/api/v1/auths/signin", {"email": "", "password": ""}, 15)["token"]
        req = urllib.request.Request(f"{webui}/api/models",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            for m in json.loads(resp.read())["data"]:
                if m["id"] == model_id:
                    return m.get("name")
    except Exception:  # noqa: BLE001 — open-webui ausente não invalida o motor
        return None
    return None


def _tabelas_consistentes(md: str) -> bool:
    """Toda linha de um bloco de tabela tem o mesmo número de colunas.

    Um `|` não escapado no texto de uma célula quebra a tabela inteira no
    renderer — e o sintoma é exatamente uma linha com colunas a mais."""
    bloco: list[int] = []
    for linha in md.splitlines():
        if linha.startswith("|"):
            bloco.append(linha.replace("\\|", "").count("|"))
        elif bloco:
            if len(set(bloco)) != 1:
                return False
            bloco = []
    return len(set(bloco)) <= 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    base = parser.parse_args().base

    print("\n[1] Modelos expostos")
    with urllib.request.urlopen(f"{base}/v1/models", timeout=30) as r:
        models = [m["id"] for m in json.loads(r.read())["data"]]
    # O ID é o que o histórico do open-webui guarda e o que as rotas usam —
    # ele NÃO muda. O nome bonito vive na camada do open-webui (ADR-0014).
    check("id `query` servido pelo Hayhooks", "query" in models, str(models))
    check("id `explore` servido pelo Hayhooks", "explore" in models, str(models))
    for id_, nome in (("query", "Indicador"), ("explore", "Explorar")):
        check(f"open-webui exibe `{id_}` como {nome}", _display_name(id_) == nome,
              f"exibindo: {_display_name(id_)!r}")

    print("\n[2] Explorar — pergunta temática")
    answer = post(f"{base}/explore/run", {"query": "O que existe sobre financiamento habitacional?"})
    a = answer.get("result", answer)
    check("responde com status sucesso", a.get("status") == "sucesso", str(a)[:120])
    check("apresenta tabelas", len(a.get("ranked_tables") or []) > 0)
    check("nenhuma tabela fora do catálogo", a.get("off_catalog_tables") == [],
          str(a.get("off_catalog_tables")))
    check("traz SQL de amostra", bool(a.get("sample_sql")))
    check("oferece o próximo passo (handoff)", "Indicador" in (a.get("next_step") or ""))

    print("\n[3] Explorar — pergunta de catálogo")
    a2 = post(f"{base}/explore/run", {"query": "Quais bases de dados vocês têm disponíveis?"})
    a2 = a2.get("result", a2)
    check("reconhece pergunta de catálogo", a2.get("catalog_scope") is True)
    check("panorama traz todos os domínios do escopo", len(a2.get("catalog_domains") or []) >= 5,
          str(len(a2.get("catalog_domains") or [])))

    print("\n[4] Explorar — renderização no chat")
    md, secs = chat(base, "explore", "Estou começando a olhar o MCMV FAR. O que existe?")
    check("resposta em tabela Markdown", "| Tabela |" in md)
    check("sem célula truncada em reticências",
          not any(l.startswith("|") and "…" in l for l in md.splitlines()))
    check("colunas consistentes em toda tabela Markdown", _tabelas_consistentes(md))
    check(f"latência aceitável ({secs:.1f}s)", secs < 40)

    print("\n[5] Indicador — resposta completa com gráfico")
    md2, secs2 = chat(base, "query", "Quantos empreendimentos tem por região?")
    check("traz SQL", "```sql" in md2)
    check("traz gráfico embutido", "data:image/png;base64" in md2)
    check("traz link do relatório .docx", ".docx" in md2)
    check("não emitiu aviso de falha de execução", "Não consegui executar" not in md2)
    check(f"latência aceitável ({secs2:.1f}s)", secs2 < 90)

    print("\n[6] Degradação — pergunta fora do escopo não pode inventar")
    a3 = post(f"{base}/explore/run", {"query": "Quero a execução orçamentária do FAR, valor executado"})
    a3 = a3.get("result", a3)
    check("não inventa tabela em pergunta sem cobertura", a3.get("off_catalog_tables") == [])
    check("ainda responde algo útil", bool(a3.get("summary")))

    falhas = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{'='*60}\n{len(CHECKS)-len(falhas)}/{len(CHECKS)} verificações OK")
    if falhas:
        print("FALHARAM: " + ", ".join(falhas))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())

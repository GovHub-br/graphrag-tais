"""Guardrails de resposta (ADR-0016).

O que este pacote garante, antes de a pergunta chegar ao retrieval e ao LLM:

- **dado pessoal não se responde** — pedido de CPF/CNPJ/nome/endereço/contato é
  recusado com a alternativa agregada;
- **o modelo não acessa o dado** — pergunta sobre acesso recebe a explicação
  correta (o modelo escreve o SQL; quem executa é o banco, somente-leitura);
- **fora do objetivo não se responde** — o que não é explorar catálogo nem medir
  indicador recebe a recusa que diz o que o sistema faz.

Uso (os dois wrappers de pipeline fazem exatamente isto):

    from rag.guardrails import check_question, guardrail_verdict

    verdict = guardrail_verdict(query)
    if verdict:
        return verdict.message

`guardrail_verdict` é o ponto de entrada de produção: lê `settings`
(liga/desliga, termos extras). `check_question` é a função pura, sem config —
é ela que os testes exercitam.
"""
from __future__ import annotations

import re
from typing import List, Optional

from rag.guardrails.policy import Verdict, check_question

__all__ = ["Verdict", "check_question", "guardrail_verdict"]


def _extra_pii_patterns(raw: str) -> List[str]:
    r"""Converte `GUARDRAIL_EXTRA_PII_TERMS` em padrões de palavra inteira.

    Os termos do env são **texto**, não regex — é o que a config promete
    ("matricula do servidor,codigo do beneficiario"). Sem `re.escape`, qualquer
    metacaractere que apareça ali vira regex ativo: no melhor caso um falso
    positivo silencioso (`nis.pis` casando "nisXpis"), no pior um `re.error`
    que derruba o gate no meio da pergunta (`matricula (siape)` — exatamente o
    formato que a docstring da config incentiva).

    Duas concessões ao termo escrito por humano: espaço casa como `\s+` (o
    termo continua valendo com espaçamento irregular) e a fronteira `\b` só
    entra onde há caractere de palavra para delimitar — `\b` depois de `)`
    nunca casa, e o termo inteiro morreria em silêncio.
    """
    patterns: List[str] = []
    for term in (raw or "").split(","):
        term = term.strip()
        if not term:
            continue
        core = re.escape(term).replace(r"\ ", r"\s+")
        prefix = r"\b" if term[0].isalnum() or term[0] == "_" else ""
        suffix = r"\b" if term[-1].isalnum() or term[-1] == "_" else ""
        patterns.append(prefix + core + suffix)
    return patterns


def guardrail_verdict(query: str) -> Optional[Verdict]:
    """`check_question` com a configuração do ambiente aplicada.

    `GUARDRAILS_ENABLED=false` desliga o gate inteiro — existe para depurar uma
    recusa suspeita sem editar código, não para rodar assim em produção.
    `GUARDRAIL_EXTRA_PII_TERMS` acrescenta termos ao filtro de dado pessoal
    (lista separada por vírgula, casada como palavra inteira): cada base nova
    traz o seu próprio vocabulário de identificador, e ele é conhecimento de
    escopo — mora em env, como o `SCOPE_GAPS`.
    """
    from rag.settings.configs import settings

    if not settings.GUARDRAILS_ENABLED:
        return None
    extras = _extra_pii_patterns(settings.GUARDRAIL_EXTRA_PII_TERMS)
    return check_question(query, extra_pii_terms=extras)

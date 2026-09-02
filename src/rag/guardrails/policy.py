"""Guardrail de entrada — decide, ANTES de qualquer LLM, se a pergunta é
respondível pelo sistema.

Três regras, na ordem em que são avaliadas:

1. **Dado pessoal** (`pii_request`) — pedido de CPF, CNPJ, nome, endereço,
   contato, nascimento… O sistema é de dado **agregado**; identificar pessoa
   nunca é resposta legítima aqui.
2. **Acesso ao dado** (`data_access`) — "você acessa o banco?", "a IA vê meus
   dados?". Não é recusa: é a resposta certa, dita de uma vez só e sempre igual
   — o modelo **escreve o SQL**, quem executa é o banco.
3. **Fora do objetivo** (`off_scope`) — nem explorar catálogo, nem medir
   indicador. Inclui tentativa de reprogramar o assistente.

Além dessas, `about_system` responde "o que você faz?" e a saudação solta.

Por que determinístico e não um classificador: a mesma razão que fez as personas
serem dois modelos no dropdown em vez de um classificador de intenção (ADR-0014)
— classificador erra em silêncio, custa uma chamada de LLM por pergunta e não é
testável sem gastar modelo. Aqui o gate roda no host, em microssegundos, e os
testes cobrem caso a caso. O que ele não pega (o off-topic criativo, a paráfrase
esperta) fica com a **segunda camada**: as regras de recusa nos prompts de
sistema das duas personas. Camada rasa e barata na frente, camada profunda e
cara atrás — nenhuma das duas sozinha.

⚠️ Este módulo é **puro**: só `re`/`unicodedata`, sem Haystack, sem serviço, sem
`settings` no import (a config é lida na chamada). É o que permite testá-lo no
host com `python -m unittest tests.test_guardrails`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

from rag.guardrails import messages


@dataclass(frozen=True)
class Verdict:
    """Decisão do gate. `blocked=True` = a pipeline NÃO roda; `message` é a
    resposta final. `reason` é o rótulo que vai para o log e para o eval."""

    reason: str
    message: str
    blocked: bool = True


def _fold(text: str) -> str:
    """Minúsculas, sem acento, espaçamento normalizado — 'endereço' vira
    'endereco' e 'Qual  o CPF' vira 'qual o cpf'. Casar por texto cru faria a
    regra falhar em metade das perguntas reais."""
    norm = unicodedata.normalize("NFKD", (text or "").lower())
    stripped = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return " " + re.sub(r"\s+", " ", stripped).strip() + " "


def _any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


# --------------------------------------------------------------------------- #
# 1) Dado pessoal
# --------------------------------------------------------------------------- #
# Termos que SÓ existem para identificar alguém. Nenhum indicador agregado
# precisa deles no resultado.
_PII_TERMS = (
    r"\bcpfs?\b", r"\brgs?\b", r"\bcnh\b", r"\bnis\b", r"\bpis\b",
    r"\bpasep\b", r"\bcns\b", r"\bcartao (do )?sus\b", r"\btitulo de eleitor\b",
    r"\bcarteira de (trabalho|identidade|motorista)\b",
    r"\bnome (completo|civil|social|do titular|da pessoa)\b",
    r"\bdata de nascimento\b", r"\bdados? pessoa(l|is)\b", r"\bpessoa fisica\b",
    r"\bendereco", r"\blogradouro\b", r"\bcep\b",
    r"\btelefone", r"\bcelular", r"\bwhatsapp\b", r"\be-?mails?\b",
    r"\bconta bancaria\b", r"\bagencia e conta\b", r"\bchave pix\b",
    # "nome" + substantivo de PESSOA. "nome" sozinho é a coisa mais comum do
    # catálogo (nome da tabela, nome do município, nome do empreendimento) —
    # bloquear o termo cru transformaria o guardrail num estorvo.
    r"\bnomes? (do|da|dos|das|de) (beneficiari|cidada|servidor|morador|"
    r"proprietari|socio|responsave|contratad|contemplad|inscrit|titular|"
    r"funcionari|aluno|paciente|cliente|pessoa)",
)

# Marcadores que dizem "a pergunta é sobre a ESTRUTURA, não sobre a pessoa":
# perguntar "quais tabelas têm CPF?" é exatamente o trabalho do modo Explorar.
_METADATA_MARKERS = (
    r"\b(tabela|tabelas|coluna|colunas|campo|campos|schema|catalogo|dicionario|"
    r"base de dados|bases de dados|dataset|metadado)\b",
    r"\bqua(l|is) (base|bases|fonte|fontes)\b",
    r"\b(existe|tem|ha|possui) (alguma )?(informacao|dado) (de|sobre)\b",
)

# Marcadores de AGREGAÇÃO: contar CPFs distintos não identifica ninguém.
_AGGREGATE_MARKERS = (
    r"\b(quantos|quantas|quantidade|total|totais|soma|somatorio|media|mediana|"
    r"percentual|proporcao|distribuicao|contagem|numero de|nº de|no de)\b",
    r"\bdistint[oa]s?\b", r"\bunic[oa]s?\b", r"\bagrupad[oa]s?\b",
    r"\bpor (uf|estado|regiao|municipio|ano|mes|periodo|situacao|faixa)\b",
)

# Pedidos que identificam pessoa mesmo vindo embrulhados em agregação ou em
# vocabulário de catálogo — "liste os CPFs da tabela X" tem "tabela" e continua
# sendo um pedido de lista de CPF. Estes vencem as exceções acima.
_PII_ALWAYS = (
    r"\b(liste|listar|lista de|relacao de|me de|me da|me diga|informe|mostre|"
    r"traga|exiba|imprima|quero|preciso d[eo]|retorne|devolva|selecione)\b"
    r"[^?]{0,40}\b(cpfs?|rgs?|nome completo|nomes|enderecos?|telefones?|"
    r"e-?mails?)\b",
    r"\bquem (e|sao|foi|foram) (o|a|os|as) (beneficiari|morador|proprietari|"
    r"contemplad|inscrit|responsave|servidor)",
    r"\bidentifi(que|car|cacao d[eoa])\b[^?]{0,30}\b(pessoa|beneficiari|"
    r"morador|cidada|titular)",
    r"\bdados? pessoa(l|is)\b",
    # "nominal/nominais/nominalmente" e "nominativa/nominata": o pedido de
    # lista COM NOME, dito no vocabulário de quem trabalha com cadastro.
    r"\bnomina(l|is|lmente)\b", r"\bnominat(iv[oa]s?|as?)\b",
    # CNPJ sozinho NÃO recusa (ver nota abaixo) — mas CNPJ colado em pessoa
    # física recusa, nas duas ordens: "CNPJ e nome dos sócios", "sócio e o CNPJ".
    r"\bcnpjs?\b[^?]{0,45}\b(socios?|representante|responsave(l|is) lega(l|is)|"
    r"titular|pessoas? fisicas?|dono|proprietari|cpfs?)\b",
    r"\b(socios?|representante|responsave(l|is) lega(l|is)|titular|"
    r"pessoas? fisicas?|dono|proprietari|cpfs?)\b[^?]{0,45}\bcnpjs?\b",
)


# CNPJ e razão social de PESSOA JURÍDICA não são, por si, dado pessoal: empresa
# contratada pelo poder público é informação pública, e "as 10 Entidades
# Organizadoras de alto risco — mostre nome, CNPJ, obras paralisadas" é caso do
# gold de negócio do projeto (`eval/datasets/retrieval/mcid-business-v0.1.0.json`),
# não um vazamento. Recusar isso quebrava uma pergunta que o sistema existe para
# responder. O que continua recusando é CNPJ **ligado a pessoa física** (sócio,
# representante, titular) — os padrões acima — e a proteção do VALOR fica onde
# deve ficar: no acesso mascarado do banco (ADR-0016).


def _is_pii_request(folded: str, extra_terms: Iterable[str] = ()) -> bool:
    if _any(_PII_ALWAYS, folded):
        return True
    terms = tuple(_PII_TERMS) + tuple(extra_terms)
    if not _any(terms, folded):
        return False
    # Termo de PII presente, mas a pergunta é sobre estrutura ou sobre agregado
    # → deixa passar. É aqui que "quantos CNPJs distintos há na base?" e "quais
    # tabelas têm CPF?" continuam funcionando.
    return not (_any(_METADATA_MARKERS, folded) or _any(_AGGREGATE_MARKERS, folded))


# --------------------------------------------------------------------------- #
# 2) Acesso ao dado
# --------------------------------------------------------------------------- #
_DATA_ACCESS = (
    r"\bvoces? (tem )?acess(a|am|o)\b",
    # Verbos de POSSE do dado, não só de leitura: "vocês armazenam os dados que
    # eu pergunto?" é a mesma pergunta que "vocês leem os dados?", e sem estes
    # verbos ela escapava do gate e recebia um panorama de catálogo como resposta.
    r"\bvoces? (acessa|acessam|le|leem|ve|veem|enxerga|enxergam|consulta|"
    r"consultam|guarda|guardam|armazena|armazenam|salva|salvam|grava|gravam|"
    r"registra|registram|retem|reten|usa|usam|treina|treinam)\b"
    r"[^?]{0,30}\b(dado|dados|banco|base|tabela|linha|pergunta|conversa|historico)",
    r"\b(a ia|a llm|o llm|o modelo|a inteligencia artificial|o chatgpt|a openai|"
    r"o gpt|voces?)\b[^?]{0,40}\b(acessa|acessar|ve|ver|le|ler|tem acesso|"
    r"ter acesso|enxerga|enxergar|consulta|consultar|guarda|guardar|armazena|"
    r"armazenar|treina|treinar|aprende|aprender)\b[^?]{0,20}"
    r"\b(dado|dados|banco|base|tabela|linha|linhas|informac)",
    r"\btem acesso (a|ao|aos|as) (dado|dados|banco|base|tabela)",
    r"\b(meus|nossos|os) dados\b[^?]{0,30}\b(vao|sao enviados|saem|trafegam|"
    r"ficam|sao guardados|sao armazenados|sao usados)\b",
    r"\benvia(m|do)? (os )?dados (para|pra|ao)\b",
    r"\bonde (os |esses )?dados (ficam|estao|sao guardados)\b",
    r"\b(lgpd|privacidade|sigilo|confidencialidade)\b",
    r"\btreina(do|mento)? com (os )?(meus|nossos|esses|estes)? ?dados\b",
    r"\bo modelo (roda|executa) (a|o) (consulta|sql)\b",
)


# --------------------------------------------------------------------------- #
# 3) Fora do objetivo
# --------------------------------------------------------------------------- #
# Tentativa de reprogramar o assistente. Fica junto do off-scope porque a
# resposta é a mesma: dizer o que o sistema faz e devolver o turno.
_INJECTION = (
    r"\bignor(e|ar|a) (as |todas as |suas )?(instruc|regras|orientac|diretriz)",
    r"\besquec(a|er) (as |suas |todas as )?(instruc|regras)",
    r"\b(system|system prompt|prompt do sistema|prompt de sistema)\b",
    r"\b(suas|as) instrucoes\b", r"\brevele\b", r"\brepita (as|o|suas)\b",
    r"\b(aja|atue|comporte-se) como\b", r"\bfinja (que|ser)\b",
    r"\bvoce (agora )?e (um|uma)\b", r"\bmodo (desenvolvedor|dev|debug)\b",
    r"\bjailbreak\b", r"\bsem (as )?restricoes\b",
)

# Pedidos que são claramente de outro produto. Lista curta e literal de
# propósito: cada padrão aqui é uma coisa que o sistema comprovadamente não faz.
# A ambiguidade fica para a camada de prompt, que tem o catálogo na mão.
_OFF_SCOPE = (
    r"\b(escreva|escreve|escrever|crie|cria|criar|gere|gera|gerar|faca|fazer|"
    r"monte|monta|montar|redija|desenvolva|programe)\b"
    r"[^?]{0,25}\b(poema|poesia|musica|letra|piada|historia|conto|redacao|"
    r"texto|carta|e-?mail|email|discurso|post|artigo|resumo do livro|receita|"
    r"codigo|script|funcao|programa|site|app|aplicativo|logo|imagem)\b",
    r"\b(traduz(a|ir)|resuma este|resuma esse|resumir este|corrija este)\b",
    r"\breceita de\b", r"\bprevisao do tempo\b", r"\bque horas sao\b",
    r"\b(quem ganhou|resultado do jogo|placar)\b",
    r"\bcomo (faco|fazer|instalo|instalar|configurar|programar)\b"
    r"[^?]{0,40}\b(python|java|javascript|docker|linux|excel|windows)\b",
    r"\bme ajude a (programar|codar|escrever codigo|estudar)\b",
    r"\bconselho (medico|juridico|financeiro)\b",
    r"\bo que voce acha (de|do|da|sobre)\b",
)

_ABOUT_SYSTEM = (
    r"\bo que voce (faz|pode fazer|sabe fazer|consegue fazer)\b",
    r"\bquem (e|es) voce\b", r"\bcomo (voce )?funciona\b",
    r"\bpara que (voce )?serve\b", r"\bqual (e )?(o )?seu objetivo\b",
    r"\bcomo (eu )?(uso|utilizo|pergunto|comeco)\b",
    r"\bqual a diferenca entre (explorar|indicador)\b",
    r"\bme ajuda\b", r"\bpode ajudar\b",
)

# Saudação SOLTA (a mensagem inteira é o cumprimento). "Bom dia, quantos
# empreendimentos há no RJ?" não cai aqui — e não deve cair.
_GREETING_ONLY = re.compile(
    r"^ ?(oi+|ola|opa|eai|e ai|hey|hi|bom dia|boa tarde|boa noite|tudo bem|"
    r"tudo bom|teste|test)[ ,.!?]* $"
)


# --------------------------------------------------------------------------- #
def check_question(query: str, extra_pii_terms: Iterable[str] = ()) -> Optional[Verdict]:
    """Avalia a pergunta. `None` = pode seguir para a pipeline.

    A ordem das regras é decisão de projeto, não acidente: "você tem acesso ao
    meu CPF?" é pergunta sobre acesso, não pedido de CPF, e por isso o bloco de
    acesso vem antes do de PII. Do mesmo modo, `about_system` vem antes do
    off-scope: "o que você faz?" é fora do objetivo, mas merece a resposta útil,
    não a recusa.
    """
    folded = _fold(query)
    if not folded.strip():
        return None  # pergunta vazia é tratada pelo wrapper, que tem o texto de boas-vindas

    if _any(_DATA_ACCESS, folded):
        return Verdict("data_access", messages.DATA_ACCESS)

    if _GREETING_ONLY.match(folded) or _any(_ABOUT_SYSTEM, folded):
        return Verdict("about_system", messages.ABOUT_SYSTEM)

    if _is_pii_request(folded, extra_pii_terms):
        return Verdict("pii_request", messages.PII_REQUEST)

    if _any(_INJECTION, folded) or _any(_OFF_SCOPE, folded):
        return Verdict("off_scope", messages.OFF_SCOPE)

    return None

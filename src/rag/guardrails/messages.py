"""Textos das respostas de guardrail — o que a pessoa lê quando o sistema recusa.

Por que aqui e não em `rag/prompts/`: `prompts/` é **texto que vai para o LLM**
(o contrato do modelo). Estes textos são o contrário — são **resposta pronta**,
escrita por nós, que sai sem passar por modelo nenhum. Misturá-los com os
prompts apagaria essa distinção, que é justamente o que dá à recusa a sua
propriedade mais importante: ela é determinística, idêntica toda vez, e não
depende de o modelo ter obedecido a uma instrução.

São curtos de propósito. A recusa que ninguém lê é a que vem em três parágrafos
de justificativa; a que funciona diz o que não dá, o que dá no lugar, e devolve
o turno para a pessoa.
"""
from __future__ import annotations

#: Pedido de dado pessoal identificável (CPF, nome, endereço, contato…).
#: CNPJ de empresa NÃO entra aqui — só quando ligado a pessoa física (ver policy).
PII_REQUEST = """\
Não respondo pedidos de **dado pessoal identificável** — CPF, nome de pessoa, \
endereço, telefone, e-mail, data de nascimento e afins.

Este assistente trabalha sobre dados **agregados** do catálogo do GovHub. \
No lugar, dá para:

- contar e somar por recorte (UF, município, período, situação);
- comparar categorias, ver distribuição e evolução no tempo;
- descobrir **quais bases e colunas existem** sobre o assunto (modo **Explorar**).

Reformule em termos agregados e eu sigo daqui."""

#: Pergunta que não é nem exploração de catálogo nem medição de indicador.
OFF_SCOPE = """\
Essa pergunta está fora do que este assistente faz.

Ele serve a dois objetivos, e só a eles:

- **Explorar** — descobrir quais bases existem sobre um assunto, o que cada uma \
responde e como começar a olhar o dado;
- **Indicador** — medir algo sobre uma base conhecida: eu escrevo a consulta \
SQL, o banco executa em modo somente-leitura e você recebe o resultado com \
gráfico e relatório.

Me diga um assunto ou um indicador do GovHub e eu sigo daqui."""

#: Pergunta sobre acesso/privacidade dos dados — o que o modelo vê e o que não vê.
DATA_ACCESS = """\
Vale esclarecer como funciona aqui, porque a diferença é importante:

- **O modelo de linguagem não acessa o banco de dados.** Ele lê apenas \
**metadados** do catálogo — nomes de tabela e de coluna, descrições, grão e \
relações — e com isso **escreve um comando SQL**.
- Quem executa o comando é o **banco**, por uma conexão **somente-leitura**, \
fora do modelo.
- O texto que explica o resultado é escrito a partir de um **resumo \
estatístico** (contagens, somas, mínimos e máximos, maiores e menores \
categorias), não das linhas.

Ou seja: o que o modelo produz é a **consulta**; o dado permanece no banco."""

#: "O que você faz?", "como funciona?", saudação solta.
ABOUT_SYSTEM = """\
Sou o assistente de dados do GovHub. Trabalho sobre o **catálogo de metadados** \
das bases e tenho dois modos, escolhidos no seletor de modelo:

- **Explorar** — "o que vocês têm sobre financiamento habitacional?" → mostro as \
bases que existem, o que cada uma serve para responder, uma amostra de linhas \
reais e o que **não** existe no catálogo.
- **Indicador** — "valor contratado por UF em 2024" → escrevo o SQL, o banco \
executa em modo somente-leitura e você recebe explicação, gráfico e relatório \
em `.docx`.

Me diga o assunto ou o indicador e eu começo."""

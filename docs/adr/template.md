# ADR-NNNN: Título da decisão

- **Status**: Proposto <!-- Proposto | Aceito | Rejeitado | Substituído por ADR-XXXX | Depreciado -->
- **Data**: AAAA-MM-DD
- **Autores**: Nome (time/projeto)
- **Revisores**: Nome (time/projeto)

> **Regra de citação**: um ADR só pode citar ADRs já existentes, com número
> menor que o seu. Não cite ADRs futuros (número maior, ainda não criados no
> momento da escrita). Se a decisão depende de algo que só um ADR futuro vai
> definir, descreva o conceito sem linkar/citar o número, e deixe o ADR futuro
> linkar de volta para este quando for criado.

## Introdução ao problema

Descreva o problema ou a força motriz que exige uma decisão. Responda:

- Qual é o contexto (técnico, organizacional, de negócio) em que a decisão surge?
- Qual dor, limitação ou oportunidade está sendo endereçada?
- Por que essa decisão precisa ser tomada *agora*?
- Quais restrições já são conhecidas (técnicas, de prazo, de recursos,
  regulatórias — ex.: LGPD, dados públicos)?

## Decisão

Declare a decisão tomada em uma ou duas frases, de forma direta. Detalhe em
seguida o suficiente para que outro time consiga implementá-la ou adotá-la sem
precisar perguntar novamente.

## Alternativas consideradas

Liste as opções avaliadas, incluindo a opção de "não fazer nada" quando
aplicável. Para cada alternativa, explique brevemente por que foi ou não escolhida.

### Alternativa A: `<nome>`

- Descrição breve.
- Prós.
- Contras.

### Não fazer nada / manter status quo

- Descrição do que aconteceria se nenhuma decisão fosse tomada.
- Por que essa opção foi ou não aceitável.

## Tradeoffs

Explicite o que se ganha e o que se perde. Marque cada item com `[Alto impacto]`,
`[Médio impacto]` ou `[Baixo impacto]` para este contexto específico, e feche com
uma **Avaliação** que declare explicitamente o julgamento.

### Vantagens

- **[Alto/Médio/Baixo impacto]** ...

### Desvantagens

- **[Alto/Médio/Baixo impacto]** ...

### Avaliação

Declare o julgamento: os ganhos superam os custos neste contexto? Por quê? Quais
desvantagens permanecem como risco ativo a mitigar?

## Consequências

- **Positivas**: o que melhora a partir desta decisão.
- **Negativas**: o que piora, ou qual débito técnico/organizacional é criado.
- **Ações decorrentes**: migrações, comunicação, mudanças de processo necessárias.

## Referências

- Links para discussões, RFCs, issues, benchmarks ou documentação externa.

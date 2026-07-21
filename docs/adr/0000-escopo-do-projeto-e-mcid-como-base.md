# ADR-0000: Escopo do projeto e MCid como base de desenvolvimento

- **Status**: Proposto
- **Data**: 2026-07-21
- **Autores**: arthrok (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

O **graphrag-tais** é um assistente de *text-to-SQL* pensado para o **GovHub**, que
reúne vários projetos de dados públicos (MinC, MCid, IPEA, entre outros). A demanda
imediata que originou o projeto é o **MinC** (Ministério da Cultura), mas o objetivo
é um RAG **genérico**, reutilizável por **qualquer projeto do GovHub** — não um
sistema acoplado a um único acervo.

Isso cria uma tensão de desenvolvimento: **sobre qual acervo desenvolver e
validar?**

- O alvo imediato (**MinC**) tem, no momento, uma base **pequena** — poucas tabelas
  e relações, insuficiente para exercitar de forma significativa a travessia de
  grafo (FK, lineage, granularidade) que é o núcleo do sistema (a estratégia de
  recuperação é detalhada em ADR próprio). Calibrar só sobre uma base pequena
  arrisca otimizar para um caso não representativo.
- No outro extremo, o **IPEA** tem um acervo **grande**, porém pesado e complexo
  demais para iterar rápido nesta fase inicial.

Precisamos decidir **agora** o projeto-base de desenvolvimento, porque ele define os
dados sobre os quais vamos iterar, medir qualidade e calibrar a recuperação.

## Decisão

1. O **graphrag-tais é um RAG genérico do GovHub**. A arquitetura não é acoplada a
   um projeto: a **entrega imediata é o MinC**, mas o sistema deve servir qualquer
   projeto do GovHub.
2. O **desenvolvimento e a validação são feitos com base no projeto do MCid**
   (Ministério das Cidades), por três motivos:
   - tem um **bom número de bases de dados** — porte **médio**: maior que a base
     (hoje pequena) do MinC e menor que a (muito grande) do IPEA —, o que dá riqueza
     estrutural suficiente para exercitar a travessia sem inviabilizar a iteração;
   - tem **boa cobertura de documentação** dos dados;
   - conta com **suporte ativo da equipe técnica do MCid**.
3. Como os metadados de todos os projetos do GovHub **centralizam numa fonte única**
   (o OpenMetadata — decisão registrada em ADR próprio), **trocar o projeto-alvo é
   essencialmente troca de configuração** (as fontes), e não reengenharia. É isso
   que torna *desenvolver sobre o MCid e entregar para o MinC* uma operação de baixo
   atrito.

## Alternativas consideradas

### Alternativa A: Desenvolver direto sobre o MinC (o alvo imediato)

- Descrição: iterar sobre a própria base de entrega.
- Prós: é o alvo; nenhum gap de generalização para ele.
- Contras: base pequena hoje — poucas tabelas/relações para validar travessia e
  ranking; risco de calibrar para um caso não representativo e de retrabalho quando
  a base crescer.

### Alternativa B: Desenvolver sobre o IPEA (base grande)

- Descrição: usar o maior acervo como base.
- Prós: volume e complexidade reais; funciona como *stress test*.
- Contras: grande e complexo demais para iterar rápido no início; ciclos de teste
  lentos; sem a mesma garantia de documentação/suporte dedicado nesta fase.

### Alternativa C: Desenvolver sobre o MCid (escolhida)

- Descrição: base de **porte médio**, bem documentada, com suporte técnico ativo.
- Por que foi escolhida: melhor equilíbrio entre **tamanho** (rico o bastante para
  validar, leve o bastante para iterar), **qualidade de documentação** e
  **disponibilidade da equipe** — o que acelera o desenvolvimento e dá metadados de
  qualidade, dos quais o sistema depende diretamente.

### Não fazer nada / não definir base

- Descrição: iterar sobre dados ad-hoc, sem base fixa.
- Por que não: sem um acervo representativo e estável, não há baseline de qualidade
  confiável para medir a evolução do retrieval.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Base **representativa** (porte médio): validação significativa
  da travessia (FK/lineage/grain) sem custo de iteração proibitivo.
- **[Alto impacto]** **Documentação + suporte** da equipe do MCid: metadados de
  melhor qualidade (crítico, já que a qualidade do sistema depende da qualidade do
  catálogo) e ciclo de feedback rápido.
- **[Médio impacto]** **Genérico via fonte única**: como tudo passa pelo
  OpenMetadata, o esforço no MCid se converte em entrega para o MinC (e outros
  projetos) com baixo atrito — troca-se a configuração, não o código.

### Desvantagens

- **[Alto impacto]** O projeto-base (MCid) **não é o alvo de entrega** (MinC): há
  risco de **vieses específicos do MCid** (convenções de nomenclatura, modelagem,
  granularidades) que não generalizem.
- **[Médio impacto]** É preciso **validar a generalização nas pontas**: base pequena
  (MinC) e base grande (IPEA) têm características (esparsidade de relações, volume)
  que o porte médio do MCid não cobre.

### Avaliação

Os ganhos superam os custos: sem uma base representativa e bem suportada não há como
calibrar a recuperação com confiança, e o MinC hoje não oferece isso. O MCid dá o
melhor equilíbrio tamanho/qualidade/suporte. O risco ativo é a **generalização entre
projetos**, mitigado por manter a arquitetura **agnóstica ao projeto** (nada do MCid
hardcoded no código) e testar cedo contra amostras de MinC e IPEA.

## Consequências

- **Positivas**: baseline de desenvolvimento representativo e bem suportado; caminho
  claro para reuso GovHub-wide.
- **Negativas**: risco de viés do MCid; necessidade de testes de generalização entre
  bases de tamanhos diferentes.
- **Ações decorrentes**:
  - Manter tudo **parametrizado por configuração** (fontes/coleções/domínios), sem
    nada específico do MCid no código.
  - Validar **cedo** contra amostras do MinC (alvo de entrega) e, quando possível,
    do IPEA (escala).
  - Alinhar com a **equipe técnica do MCid** o acesso e a qualidade dos metadados.

## Referências

- Processo e convenção de ADRs deste repositório: [README](./README.md) e
  [template](./template.md).

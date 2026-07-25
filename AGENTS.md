# Instruções do projeto

## Gate MDD do retrieval

Use a skill do projeto em
`skills/evaluate-retrieval-change/SKILL.md` para qualquer alteração capaz de
mudar a recuperação:

- conteúdo ou versão dos cards e modelos de embedding;
- busca densa, esparsa, fusão híbrida, filtros, `top_k` ou reranking;
- escolha e ordenação de sementes;
- expansão, travessia ou poda no Neo4j;
- representação de relacionamentos e caminhos;
- contrato do `RetrievalTrace`, datasets gold, métricas ou políticas em `eval/`.

Execute o perfil `quick` antes e depois da implementação e o perfil `full`
antes de considerar a mudança concluída. Nunca promova automaticamente um
resultado para baseline nem altere o gold apenas para fazer um gate passar.

Mudanças que não afetam retrieval não exigem o benchmark completo.

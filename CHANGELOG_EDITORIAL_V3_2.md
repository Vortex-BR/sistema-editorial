# Editorial Intelligence V3.2 — ordem, profundidade e prosa humana observável

Data: 18/07/2026

## Motivo da atualização

O primeiro conteúdo real confirmou que o pipeline conseguia pesquisar, escrever e
entregar um rascunho, porém ainda produzia um corpo curto, condensado e previsível.
Os títulos estavam mais naturais que os parágrafos. A abertura também antecipava
faixas e recomendações antes de apresentar os métodos e construir um modelo mental
para o leitor.

Esta versão não tenta “enganar detector de IA”. Ela corrige defeitos editoriais que
qualquer revisor humano consegue observar no texto.

## Alterações principais

- Nova ordem do contrato procedural:
  `fundamento -> visão geral dos métodos -> condições comuns -> comparação -> escolha -> execução`.
- A abertura deve situar a decisão do leitor e mencionar pelo menos dois métodos
  antes de concentrar números, faixas e riscos.
- A seção de condições comuns precisa explicar importância, mecanismo, aplicação,
  manutenção, falta, excesso e ajuste — não apenas listar valores.
- O Writer recebe a sequência editorial, os critérios de abertura e um perfil de
  desenvolvimento do corpo.
- Diagnóstico determinístico de:
  - abertura numérica prematura;
  - métodos apresentados tarde demais;
  - parágrafos-resumo;
  - excesso de subtítulos para pouco corpo;
  - repetição de aberturas sintáticas;
  - cadência e formato de parágrafos uniformes;
  - visão geral e condições comuns superficiais.
- Falhas editoriais reparáveis seguem para os editores depois de uma tentativa do
  Writer. Somente falhas estruturais essenciais bloqueiam antes da edição.
- O editor localizado recebe os blocos vizinhos e o outline do artigo, evitando
  reescritas isoladas sem continuidade.
- Skills de Writer e editor de linguagem atualizadas para preservar os títulos que
  funcionam e elevar o corpo ao mesmo nível de especificidade.
- Rubrica procedural atualizada para `quality-rubric.procedural-guide.v3`.
- Migration `0031` amplia o Writer `gpt-5.4` para 20.000 tokens de saída e 300 s de
  timeout. O JSON rastreável usa muito mais tokens que o artigo visível; o limite
  anterior incentivava compressão e aumentava risco de truncamento.

## Segurança editorial preservada

- Nenhuma experiência pessoal falsa, erro deliberado, gíria aleatória ou opinião
  inventada é adicionada para “parecer humano”.
- Frases factuais continuam exigindo claims aprovados.
- Métodos, passos, links e seções obrigatórias continuam determinísticos.
- A publicação continua dependendo de revisão humana.

# Status da Editorial Intelligence V3

Data: 21/07/2026

## Atualização V3.8 — Geração incremental e retomada segura

A V3.8 transforma o Writer em um fluxo incremental: cada seção do blueprint é gerada, validada, reparada quando necessário e persistida antes da próxima. Checkpoints possuem identidade por unidade, a retomada aceita apenas um prefixo válido da sequência editorial e não repete seções concluídas. A montagem final usa ordem, posições e IDs determinísticos; o grafo limita transições e bloqueia estado inválido, mutação direta de estágio e checkpoints incompatíveis. O orçamento de palavras e blocos é distribuído antes das chamadas, impedindo que as unidades ultrapassem os limites do artigo completo. Não existe migration nova; o head permanece `0036`.

## Atualização V3.7.4 — Research Coverage & Synthesis Recovery

A V3.7.4 corrige a execução que alcançava a síntese com falsos gaps de diversidade/papel de fonte e podia perder todo o Fact Ledger após um `TypeError`. Papéis equivalentes passam a ser avaliados por capacidade, fontes profundas podem sustentar outros nós semanticamente compatíveis, nós de apoio exigem uma fonte enquanto nós core preservam duas, e a extração falha por documento sem apagar claims válidos. A síntese continua proibida quando a cobertura atual não passou. Não existe migration nova; o head permanece `0036`.

## Atualização V3.6.3 — Briefing Simplification & Manifest Safety Fix

A V3.6.3 remove a camada editorial de jurisdição/conformidade do formulário, do payload, do contrato de conhecimento, da intenção de pesquisa e da seleção de mercados. A seleção passa a depender somente do idioma/locale do projeto e do papel de evidência. O campo legado do banco é removido pela migration `0036`, enquanto payloads antigos são normalizados para permitir retomada segura.

O limite do assunto factual passa de 240 para 1.000 caracteres e a campanha MSB é testada contra os limites do formulário. O scanner do manifesto deixa de confundir metadados como `credential_verification_required_before_activation` com segredos reais. Segredos continuam bloqueados, mas o diagnóstico retorna somente o caminho seguro do campo, nunca o valor.

## Atualização V3.6.2 — Execution Reliability & Campaign Presets

A V3.6.2 fecha o fluxo operacional entre criação do projeto, criação do run, fixação do manifesto e publicação no broker. Com início automático marcado, a transação só é confirmada quando projeto, evento, run e manifesto existem; falhas anteriores a esse ponto fazem rollback. Indisponibilidade transitória do broker não apaga o run: o dispatch fica registrado para retry.

A prontidão e o manifesto agora usam papéis específicos por versão, impedindo dependências exclusivas da V3 de bloquearem a V2. A interface possui preflight com reparação segura, exibe dependências acionáveis, recupera projetos legados sem run e inclui a campanha `MSB — Germinação no papel-toalha`. A migration `0036` remove o campo legado de jurisdição; o head atual é `0036` e o contrato editorial continua V3.6.1.

## Atualização V3.6.1 — Editorial Intelligence Flow Integrity

A V3.6.1 corrige as falhas de integridade identificadas no núcleo V3.6. Perguntas críticas só contam como cobertas por claims semanticamente alinhados, autorizados e rastreáveis; o Writer recebe um plano pergunta→evidência; cada sentença recebe `sentence_id`; o rascunho precisa declarar e comprovar suas respostas; revisões invalidam a aprovação anterior; e o Quality Gate só promove o artefato cujo hash e versão foram realmente validados.

O Intelligence Gate agora classifica falhas recuperáveis e pode retornar à pesquisa direcionada antes de bloquear. Claims equivalentes são canonicalizados por `support_group`, enquanto evidências disputadas permanecem no grafo com política segura. A migration `0035` adiciona os vínculos persistentes necessários. Uma execução antiga não deve ser retomada como V3.6.1: crie um run novo depois do deploy e da migration.

## Atualização V3.6 — Editorial Intelligence Core

A V3.6 iniciou o Motor de Inteligência Editorial com estado canônico, mapa de perguntas, planos de seção, grafo de evidências e snapshots. Esses componentes permanecem como base da V3.6.1.

## Atualização V3.5.1 — Generation Hardening

A V3.5.1 corrige o transporte de contexto, o fact-check, as revisões, o briefing, o idioma, o SEO, as estruturas tipadas e a promoção final. Esses controles permanecem ativos abaixo do novo núcleo de inteligência.

## Atualização V3.5

A descoberta de fontes agora é orientada por intenção factual, locale, jurisdição e papel de evidência. Consultas são localizadas por mercado; requests, retries, fetches, créditos e timeout possuem orçamento independente; provedores têm circuit breaker; a leitura valida redirects e limita bytes; e o novo gate de cobertura pode executar recuperação direcionada antes do bloqueio. A redação continua proibida sem evidência suficiente.

A V3.5 não cria migration. Runs anteriores preservam seus manifestos e devem ser substituídos por uma nova execução para usar a política `intent-aware-search.v3.5`. A V3.6.3 removeu a antiga priorização por jurisdição e mantém a ordem por locale e função de evidência.


## Estado desta versão

A V3 está implementada como pipeline executável e opt-in. A V2 permanece
preservada para rollback e para projetos que não selecionarem
`editorial_pipeline_version=v3`.

A execução V3 só começa quando as duas flags estão ativas e a migration head `0036`
foi aplicada:

```env
EDITORIAL_PIPELINE_V3_ENABLED=true
EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED=true
```

## Pipeline entregue

```text
contrato editorial
→ arquitetura determinística do conhecimento
→ gate de ordem e dependências
→ planejamento de inteligência editorial e mapa de perguntas
→ pesquisa por função de evidência orientada pelas perguntas
→ intenção factual, mercados e consultas localizadas
→ descoberta de fontes com orçamento e circuit breaker
→ leitura segura de HTML/PDF
→ gate de cobertura e recuperação direcionada
→ classificação e rejeição de fontes comerciais
→ extração de claims contextualizados
→ triangulação e pesquisa suplementar de lacunas
→ inventário de métodos
→ referências externas independentes
→ dossiês de métodos e seções
→ matriz de decisão condicional
→ grafo de evidências e autorização de claims por seção
→ gate de inteligência editorial
→ gate de completude
→ Writer procedural
→ editor de desenvolvimento
→ fact-checker
→ editor de linguagem com preservação estrutural
→ fact-check pós-edição
→ gate de briefing, idioma, SEO e links
→ similaridade e canibalização
→ finalização rastreável
→ rubrica procedural/universal
→ promoção dos campos finais
→ revisão humana obrigatória
```

## Pesquisa e política de fontes

- O ranking da busca serve somente para descoberta.
- Produto, categoria, marketplace, loja, landing comercial, carrinho e checkout
  são rejeitados.
- Blog de e-commerce ou fabricante é `comparison_only` e nunca conta como
  autoridade, diversidade independente ou referência externa recomendada.
- Alegações comerciais exigem duas fontes independentes e não comerciais.
- Literatura científica, instituições e repositórios acadêmicos são priorizados
  para mecanismos e claims críticos.
- Guias técnicos independentes podem sustentar sequência e observações, mas são
  corroboradores: não viram autoridade científica apenas por serem detalhados.
- Notícias e enciclopédias servem para contexto, terminologia e descoberta.
- Fóruns e comunidades servem para descobrir dúvidas, não como prova técnica.

## Inteligência procedural

A V3 não envia uma lista plana de fatos ao Writer. Antes da redação, ela exige:

- sequência lógica completa do estado inicial ao resultado final;
- inventário de métodos sem duplicações artificiais;
- ação, propósito, preparação, observações, problemas, correções e condição de
  avanço em cada etapa;
- comparação e escolha condicionais, sem declarar um método universalmente
  superior;
- decisão de transição e acompanhamento até o resultado prometido;
- link externo independente e verificado para cada método;
- ausência de lacunas essenciais.

## Gate de cobertura e reparação dirigida

- O briefing procedural declara os métodos obrigatórios por nome.
- O Research Planner cria consultas específicas para cada método.
- Cada método exige ao menos três claims aprovados, três passos e referência
  externa independente antes do Writer.
- O Writer identifica os blocos com `method_id`, cobre todos os dossiês e recebe
  uma faixa de tamanho adaptada ao número de métodos e seções.
- A sequência editorial apresenta os métodos antes das condições técnicas, e a
  abertura é bloqueada quando despeja números antes de orientar o leitor.
- O corpo é avaliado por compressão em parágrafos-resumo, excesso de headings,
  repetição de aberturas e uniformidade de cadência; os sinais são editoriais e
  não tentam adivinhar a origem humana ou artificial do texto.
- Uma única reparação estrutural recebe os blockers exatos; se ainda houver
  ausência essencial, o fluxo para antes dos editores e preserva o diagnóstico.
- Um blocker crítico limita o score exibido a 59%, evitando falsa sensação de
  aprovação por média.

## Humanização editorial

- Skills V3 são fixadas no manifesto de execução para reprodutibilidade.
- Writer recebe dossiês, matriz de decisão, perfil de voz e claims aprovados.
- Transições editoriais podem existir sem evidência quando não adicionam fatos.
- Edição de desenvolvimento, fact-checking e edição de linguagem são separadas.
- Revisões são localizadas; mudança de significado volta ao fact-checker.
- A rubrica combina avaliação dos revisores com métricas determinísticas de
  cadência, uniformidade, excesso de headings, metanarração e linguagem de
  template.
- Nenhum artigo V3 é publicado automaticamente: aprovação humana final é
  obrigatória.

## Persistência e auditoria

A migration `0029` adiciona os documentos estruturados, claims contextualizados,
dossiês, matriz, revisões e avaliação procedural. A migration `0030` reconcilia
preços e perfis das rotas OpenAI selecionadas. A migration `0031` atualiza a
rubrica procedural para a versão de prosa humana observável e amplia o Writer
`gpt-5.4` para 24.000 tokens de saída na rota OpenAI selecionada; revisores recebem orçamentos próprios de até 12.000 tokens. A migration `0032` adiciona a hierarquia
tipada e os vínculos de nós usados pelo contrato editorial. A migration `0033` preserva estruturas tipadas de tabelas e callouts em
`article_blocks.structured_payload`. A migration `0034` adiciona snapshots versionados
do Motor de Inteligência Editorial em `editorial_intelligence_snapshots`. A migration
`0035` adiciona `canonical_claim_id`, `logical_sentence_id`, hash do artefato validado,
versão do artigo e revisão do draft aos vínculos persistentes. O pipeline preserva:

- checksums;
- IDs de run, projeto, contrato, fonte, claim e versão do artigo;
- snapshots de fontes;
- avaliações de fonte;
- tentativas de provedor e custos;
- checkpoints para retomada;
- relatório de fontes e rastreabilidade por sentença.

## Pendências operacionais antes do rollout total

O código está implementado, porém estes itens dependem do ambiente real:

1. executar a cadeia completa de migrations em PostgreSQL 17 com pgvector;
2. validar Redis, Celery Worker/Beat, leases, cancelamento e retomada;
3. executar busca real e chamadas aos provedores configurados;
4. rodar o benchmark editorial com múltiplas gerações e revisão humana às cegas;
5. calibrar voz, orçamento e limites usando artigos aprovados da marca;
6. fazer rollout canário antes de liberar produção em lote.

Esses itens são validação operacional/editorial; não representam módulos de
código ausentes no pipeline V3 desta versão.

# Changelog — Editorial V3.6.1

Data: 20/07/2026  
Codinome: **Editorial Intelligence Flow Integrity**

## Objetivo

A V3.6.1 corrige falhas estruturais encontradas na auditoria da V3.6. O foco desta
versão não é adicionar novos agentes, mas garantir integridade entre planejamento,
pesquisa, evidência, redação, revisão e promoção final.

## Correções críticas

### Cobertura de perguntas

- Removido o fallback que considerava qualquer claim da mesma seção como resposta.
- Criado `QuestionClaimCoverage` com status explícito:
  `unsupported`, `candidate`, `semantically_supported` e `human_overridden`.
- Perguntas críticas de pesquisa só liberam o Writer quando possuem claim:
  - autorizado na seção;
  - compatível com o papel de evidência;
  - semanticamente alinhado;
  - ligado a fonte e fato persistido;
  - permitido para escrita direta ou condicional.
- Criado `QuestionAnswerRecord` para ligar pergunta, sentenças, claims e status da
  resposta.
- O draft final bloqueia quando uma pergunta crítica não foi respondida de forma
  direta e rastreável.

### Claims canônicos e conflitos

- Adicionado `canonical_claim_id` persistente a `v3_knowledge_claims`.
- Claims equivalentes são agrupados por `support_group` validado, preservando todos
  os `source_claim_ids`, fatos e fontes independentes.
- Divergências de seção, função de evidência, grupo ou texto geram problemas de
  integridade e impedem uso direto.
- Claims disputados ou insuficientes permanecem no Evidence Graph como contexto,
  sem serem liberados como conclusão direta.
- Conflitos deixam de desaparecer antes da construção do grafo.

### Recuperação orientada pelo Intelligence Gate

- Blockers passaram a ser classificados como `recoverable`, `contract_error`,
  `nonrecoverable` ou `budget_exhausted`.
- Falhas exclusivamente recuperáveis criam tarefas por `question_id` e retornam ao
  estágio `targeted_source_recovery`.
- O fluxo relê as novas fontes, refaz a síntese, reconstrói o Evidence Graph e
  executa novamente o Intelligence Gate.
- Limite de duas rodadas de recuperação de inteligência impede loops infinitos.
- Motivos, queries, rounds, provedores e resultados ficam registrados nas métricas
  e eventos do run.

### Sentenças e fact-check

- Toda sentença recebe `sentence_id` estável.
- Sentenças podem declarar `question_ids` e `answer_status`.
- O fact-check usa `sentence_id`, `block_id`, texto exato e claims exatos; frases
  duplicadas não colidem mais.
- Revisores não podem adicionar, remover, dividir, fundir ou redistribuir sentenças
  sem invalidar a aprovação anterior.
- Claims independentes não podem mais ser concatenados para fabricar suporte
  “Frankenstein”. Frases compostas precisam ser divididas em proposições atômicas.
- A rastreabilidade exportada inclui `sentence_id`, perguntas, status de resposta,
  claims e números das fontes.

### Ciclo de vida e promoção

- Adicionado o estado `draft_pending_validation`.
- Toda mutação do Writer ou revisor invalida o hash previamente aprovado.
- Snapshots passam a guardar:
  - `validated_artifact_hash` SHA-256;
  - `article_version_id`;
  - `draft_revision`.
- O Quality Gate recusa promoção quando o hash do draft atual, a versão do artigo e
  o snapshot validado não correspondem exatamente.
- `logical_sentence_id` preserva a identidade da sentença entre versões imutáveis
  do artigo, com unicidade por bloco/versão.

### Planejamento de pesquisa e contexto

- O plano reserva slots para consultas derivadas de perguntas críticas, mesmo
  quando a tarefa já possuía seis consultas antigas.
- O Writer recebe `question_evidence_plan` e catálogo de políticas de claims.
- Criado `ContextBudgetPlanner` para reduzir duplicações antes da chamada ao modelo.
- A compactação preserva draft, perguntas, claims usados e relações factuais; se o
  contexto ainda exceder o limite, o pipeline bloqueia de forma explícita em vez de
  truncar silenciosamente.
- Revisores também recebem orçamento e compactação determinística próprios.

### Regras editoriais e schemas

- Proibições específicas de cada seção são aplicadas no draft.
- Relações cruzadas inválidas entre seção, pergunta, claim e conflito são rejeitadas
  pelo schema.
- `allowed_claim_ids` e `prohibited_claim_ids` precisam ser disjuntos.
- O detector factual foi ampliado para predicados declarativos comuns em português,
  inglês e espanhol, mantendo exclusões para metanarração editorial.
- Critérios de conclusão permanecem sinais de completude da pergunta; não são mais
  transformados em perguntas artificiais que exigiriam claims próprios.


### Correções finais de identidade, detecção e exportação

- O `canonical_claim_id` persistido é preservado quando o registro já pertence a um
  grupo canônico migrado; registros antigos sem identidade explícita continuam sendo
  canonicalizados de forma determinística.
- A normalização do `support_group` passou a usar texto normalizado, evitando IDs
  diferentes por acentuação, caixa ou variações Unicode equivalentes.
- O detector factual deixou de tratar conectivos editoriais como “e”, “é”, “saiba
  mais” e transições semelhantes como fatos isolados. Comparações verificáveis como
  “mais eficiente do que” continuam sendo classificadas como factuais.
- O override `node_resolution.research_required` agora governa de forma consistente
  tanto a seção quanto suas perguntas, impedindo estados contraditórios.
- O relatório final de fontes recebe o binding exato do artigo aprovado após o
  Quality Gate, com hash, versão e revisão correspondentes ao artefato promovido.
- A exportação pública sanitiza o binding e a rastreabilidade de sentenças, sem
  expor estruturas privadas do estado interno.

## Migration

Nova migration obrigatória:

```text
0035_editorial_intelligence_flow_integrity.py
```

Ela adiciona:

- `v3_knowledge_claims.canonical_claim_id`;
- `editorial_intelligence_snapshots.validated_artifact_hash`;
- `editorial_intelligence_snapshots.article_version_id`;
- `editorial_intelligence_snapshots.draft_revision`;
- `sentence_claims.logical_sentence_id`;
- índices, FK e constraints correspondentes.

O head esperado passa a ser `0035`.

## Compatibilidade

- A V2 permanece preservada.
- A V3.5 e a V3.5.1 continuam fornecendo pesquisa resiliente e hardening da
  geração.
- Runs criados antes da V3.6.1 não devem ser retomados como V3.6.1.
- Após o deploy e a migration, crie uma execução nova.

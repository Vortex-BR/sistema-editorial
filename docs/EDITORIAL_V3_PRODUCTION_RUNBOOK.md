# Runbook de produção — Editorial V3.8

## Geração incremental V3.8

Mantenha `V3_INCREMENTAL_WRITER_ENABLED=true`, `V3_WRITER_SECTION_REPAIR_ATTEMPTS=1` e `V3_GRAPH_MAX_TRANSITIONS=96`. O Writer deve emitir progresso e persistir um checkpoint após cada seção. Cada unidade precisa respeitar as faixas alocadas de palavras e os limites mínimo e máximo de blocos; a soma dos tetos não pode ultrapassar 300 blocos. Em staging, faça pelo menos um teste de retomada interrompendo o worker depois de uma unidade concluída e confirme que a continuação inicia na próxima seção, sem repetir chamadas pagas.

A V3.8 fixa novos contratos no manifesto. Não retome com a imagem nova um run V3 iniciado sob manifesto anterior. Finalize os runs ativos antes do deploy ou crie novos runs depois da atualização. O Alembic permanece em `0036`.

Códigos novos de diagnóstico: `V3_GRAPH_TRANSITION_LIMIT_EXCEEDED`, `V3_GRAPH_STAGE_MUTATION`, `V3_CHECKPOINT_INVALID` e `V3_CHECKPOINT_INVARIANT_VIOLATION`. Qualquer um deles exige correção da causa; não aumente o limite ou altere o checkpoint manualmente para forçar a continuação.

## Pré-voo de criação V3.6.2

Antes do teste canário, abra **Configuração** e execute **Verificar e corrigir** para V2 e V3. A readiness deve exibir `execution_dependencies: ready`. O endpoint de criação com início automático só confirma a transação quando também existe um `pipeline_run_id`.

Se um projeto antigo aparecer sem último run, abra-o e use **Iniciar execução**. Não recrie o briefing. Falhas transitórias do broker aparecem como `retry_scheduled` e são recuperadas pelo dispatcher durável/Beat.

A campanha **MSB — Germinação no papel-toalha** pode ser aplicada na tela Novo conteúdo. Ela preenche o briefing, mas não ignora o preflight nem os gates editoriais.



## Pesquisa V3.5 — pré-voo e canário

Antes de iniciar um run V3.5:

1. mantenha ao menos uma credencial Tavily ou Serper ativa e verificada;
2. configure os limites `V3_MAX_SEARCH_PROVIDER_REQUESTS`, `V3_MAX_SEARCH_PROVIDER_RETRIES`, `V3_MAX_SEARCH_ESTIMATED_CREDITS`, `V3_SOURCE_DISCOVERY_TIMEOUT_SECONDS`, `V3_MAX_SOURCE_FETCHES`, `V3_MAX_SOURCE_RECOVERY_ROUNDS` e `V3_MIN_CANDIDATE_RELEVANCE`;
3. faça deploy de uma nova imagem e crie uma nova execução — não retome um run bloqueado com manifesto anterior;
4. confirme no painel “Pesquisa V3.5” que o mercado local, consultas traduzidas, fallback e cobertura foram registrados;
5. em staging, teste 401/403, 429, timeout, fallback e redirecionamento SSRF antes do rollout.

O run só usa credenciais fixadas no manifesto como ativas e previamente verificadas. A chamada real ainda pode detectar revogação ou indisponibilidade; nesse caso, o circuit breaker isola o provedor e tenta o fallback dentro do orçamento.

Códigos principais: `V3_SOURCE_POLICY_REJECTED_ALL`, `V3_SOURCE_DIVERSITY_INSUFFICIENT`, `V3_SOURCE_FETCH_EXHAUSTED` e `V3_RESEARCH_COVERAGE_INCOMPLETE`. Use o relatório de cobertura e `exhausted_by` para distinguir falta de resultados, rejeição por política e orçamento esgotado.

## Pré-deploy

1. Faça backup do PostgreSQL.
2. Confirme uma única réplica do App all-in-one.
3. Confirme PostgreSQL com pgvector e Redis acessíveis internamente.
4. Valide as credenciais e rotas de modelos no painel.
5. Execute a suíte backend e frontend.
6. Confirme `alembic heads` retornando somente `0036`.

## Deploy

O entrypoint aplica `alembic upgrade head` antes de liberar readiness. O head atual
é `0036`: a migration `0032` adiciona a hierarquia editorial, a `0033` preserva
payloads estruturados de tabelas e callouts, a `0034` cria
`editorial_intelligence_snapshots` e a `0035` adiciona canonicalização de claims,
IDs lógicos de sentença e o vínculo hash/versão do rascunho validado. A migration
`0032` adiciona:

- `research_plans.hierarchy_json`;
- `research_questions.node_ids`;
- prioridade máxima 20 para perguntas do V2.

Não reduza o head esperado no workflow Docker. O CI e a readiness devem usar `0036`.

## Canário recomendado

Ative a V3 inicialmente em novos runs controlados. Use pelo menos um projeto de cada tipo editorial:

- explicativo;
- procedural;
- comparação;
- troubleshooting;
- educação comercial.

Verifique em cada run:

- contrato de hierarquia fixado;
- snapshot `intelligence_planner` com perguntas e planos de seção;
- snapshot `evidence_graph_builder` com claims canônicos, fontes e conflitos;
- `question_coverage` sem fallback por mera coincidência de seção;
- `intelligence_gate` em `writer_ready`, ou recuperação direcionada registrada;
- sentenças factuais com `sentence_id`, `question_ids` e claims atômicos;
- snapshot final com `validated_artifact_hash` e `article_version_id` do artefato promovido;
- perguntas vinculadas a nós;
- cobertura de evidência por nó;
- ordem do blueprint;
- blocos do Writer com `node_ids`;
- ausência de métodos artificiais em conteúdo não procedural;
- blockers do quality gate;
- custo e latência;
- decisão humana final.

## Runs existentes

Runs antigos não devem ser reescritos com o contrato novo. Retomadas continuam usando o manifesto e os artefatos fixados anteriormente. Crie um novo run para utilizar a V3.8.

## Diagnóstico

### `research_nodes_missing`

O Planner não vinculou perguntas a todos os nós factuais obrigatórios. O pipeline deve reparar ou bloquear antes da pesquisa.

### `blueprint_nodes_missing`

O blueprint omitiu uma função obrigatória. Não permita que o Writer compense isso livremente.

### `draft_node_order_invalid`

Os blocos não respeitam a sequência do contrato. Execute o reparo estrutural dirigido; se persistir, bloqueie.

### `draft_hierarchy_depth_inverted`

Uma parte periférica recebeu desenvolvimento maior do que uma parte central. Revise o blueprint ou o Writer.

### `nonprocedural_method_leakage`

Um tipo não procedural inventou métodos, passos ou matriz procedural. Verifique o tipo editorial, o prompt fixado e a rota do Writer.

## Rollback

A aplicação pode ser revertida somente se o código anterior for compatível com as colunas adicionais. O downgrade da migration remove os campos e reduz a prioridade máxima para sete. Não execute downgrade se houver runs novos que dependam de `hierarchy_json` ou `node_ids`.

A opção mais segura é manter a migration e reverter apenas a imagem da aplicação após confirmar compatibilidade de leitura.

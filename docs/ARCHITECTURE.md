# Arquitetura editorial e invariantes operacionais

## Identidades e limites

`Project` representa o objetivo editorial persistente. `PipelineRun` representa uma
tentativa específica e é a fonte de verdade para status, estágio, tentativa, erro,
retry e checkpoint. Os campos de status e estágio em `Project` permanecem apenas
como resumo compatível com a interface existente.

Uma execução nunca consulta planos, fatos, handoffs, chamadas de agentes ou
checkpoints de outra execução. Fontes mantêm identidade global pela URL canônica,
mas cada coleta cria um `SourceSnapshot` associado ao run. Reutilização futura de
snapshot deve preencher `reused_from_snapshot_id`; não existe reutilização
silenciosa no pipeline atual.

Tipos de conteúdo reconhecidos: `article`, `existing_article_update`,
`institutional_page`, `service_page`, `landing_page`, `category_page`,
`product_page` e `product_description`. Somente o pipeline de artigo está
implementado nesta revisão. O tipo fica no projeto e no artefato persistido para
que novos finalizadores não dependam do estado do projeto.

## Estados do pipeline run

Transições permitidas:

- `queued -> running | cancelled | failed`
- `running -> waiting_retry | needs_review | failed | cancelled | completed`
- `waiting_retry -> running | failed | cancelled`
- `needs_review`, `failed`, `cancelled` e `completed` são terminais

`needs_review` representa uma interrupção editorial deliberada: o run recebe
`finished_at`, mas não `failed_at`, `error_code` ou retry automático. O projeto
também permanece em `needs_review`, permitindo que filas, alertas e métricas
distingam revisão humana de falha técnica. Uma retomada editorial futura cria
uma nova execução vinculada ao mesmo projeto, preservando a tentativa original.
A migration `0007` também corrige runs históricos identificados pelo evento
`pipeline.needs_review`, registrando uma transição de correção auditável em vez de
reescrever silenciosamente o histórico.

Todas as transições passam por `PipelineRunService.transition(run_id, ...)`. O
método público carrega a linha com `SELECT ... FOR UPDATE`, adquire o advisory
lock transacional, valida o proprietário e a validade do lease quando fornecido,
e pode rejeitar um `expected_lock_version` obsoleto antes de incrementar a versão.
A variante `_transition_locked` é interna e somente é usada por operações que já
adquiriram a linha na mesma transação. Assim, `lock_version` funciona como uma
pré-condição explícita contra chamadores obsoletos, combinada ao bloqueio
pessimista, e não é descrita como um controle otimista implícito. As transições
criam `PipelineStateTransition` com origem, motivo, estágio, horário e código de
erro. Alterar diretamente o estado do run fora desse serviço é uma violação do
contrato arquitetural.

## Cancelamento cooperativo

`PipelineRun.cancellation_requested_at` é o sinal durável introduzido pela
migration `0010`. Em `queued` e `waiting_retry`, o pedido transiciona o run
imediatamente para `cancelled`, remove retry, reserva de dispatch e lease, e grava
transição e evento. Em `running`, o pedido mantém o lease e o status até a próxima
fronteira segura, permitindo que a transação em andamento termine.

O executor verifica o sinal antes de cada estágio, antes e depois de provedores
externos e após cada checkpoint. Ao encontrá-lo, descarta somente o trabalho ainda
não commitado, transiciona o run para `cancelled` e encerra a tarefa. A máquina de
estados também bloqueia qualquer tentativa posterior de gravar `completed`,
`failed` ou `waiting_retry`. Se o worker desaparecer, o reaper honra o pedido ao
expirar o lease, em vez de reenfileirar o run. Beat, Worker e os demais runs não
são interrompidos.

## Cobertura determinística da pesquisa

Cada `ResearchQuestion` recebe importância `core`, `supporting` ou `optional`.
Perguntas centrais são obrigatórias para liberar o writer; lacunas de suporte ou
opcionais permanecem visíveis como avisos e não são escondidas por uma média
geral. O gatekeeper cruza cada `approved_fact_id` recomendado com `project_id`,
`pipeline_run_id` e `research_question_id`; fatos inexistentes, de outro run, de
outro plano ou já substituídos não contam como evidência.

A aprovação exige cobertura das perguntas centrais, diversidade mínima entre os
fatos selecionados e ausência de conflitos ativos. O resultado determinístico
sobrescreve `coverage_by_question`, `missing_questions` e o score de diversidade
do modelo. Em decisão insuficiente, nenhum fato recebe aprovação global, mas
cada pergunta mantém `covered` ou `uncovered` conforme sua evidência real para
orientar o próximo ciclo. Antes de iniciar, o writer recalcula a mesma invariante
a partir dos fatos aprovados no banco e falha fechado se houver divergência.

## Idempotência

| Operação | Estratégia durável |
| --- | --- |
| Criação de projeto | `Idempotency-Key` e unique constraint global |
| Disparo do pipeline | unique `(project_id, idempotency_key)` e reutilização do run ativo |
| Entrega Celery duplicada | reserva de despacho tokenizada e lease persistente por run |
| Chamada de agente | UUID determinístico por run/papel/ciclo e retorno do resultado já concluído |
| Plano | chave `planner` única dentro do run |
| Fato | unique `(pipeline_run_id, source_id, claim_text)` |
| Snapshot | unique `(pipeline_run_id, source_id, content_hash)` |
| Handoff | sequência monotônica e chave por origem/destino/ciclos/produtor/tentativa |
| Evento | chave por operação e contador serializado no projeto |
| Checkpoint | sequência monotônica e chave por estágio/ciclos/tentativa/contrato |
| Versão | chave derivada da chamada do redator |
| Bloco | novo UUID físico; identidade lógica preservada separadamente |

Consultas anteriores ao insert servem apenas como atalho. As constraints do
PostgreSQL são a proteção final contra concorrência.

## Concorrência e eventos

O acionamento bloqueia a linha do projeto. A execução usa um advisory lock
transacional durante a aquisição e um lease persistente durante chamadas externas
e commits intermediários. O lease é renovado a cada checkpoint.

Eventos não usam `MAX(sequence) + 1`. `EventService` bloqueia a linha do projeto,
incrementa `projects.event_sequence` e persiste o evento na mesma transação. A
ordenação está indexada por projeto, run, sequência e horário.

Descoberta editorial exige Redis para o lock distribuído. Se Redis falhar, a
descoberta falha explicitamente e o pipeline principal continua. O lock contém
token de propriedade e só é removido pelo proprietário.

## Limites transacionais

- criação do run e transição inicial;
- resultado persistido, handoff e checkpoint antes do avanço observável;
- versão, blocos, sentenças e evidências;
- aprovação editorial de versão, sentenças e evidências;
- pacote final, impressão de similaridade e evento;
- erro, decisão de retry, próximo horário e evento de falha;
- ativação de configuração continua em uma única transação da API.

Chamadas externas são registradas antes e depois separadamente para que uma queda
durante o provedor permaneça observável. Ao retomar, uma chamada já concluída é
reutilizada pelo mesmo run; resultados editoriais de outro run nunca são usados.

## Checkpoints e recuperação

Depois de cada transição do grafo é salvo um `PipelineCheckpoint` com:

- estágio concluído e próximo estágio;
- estado serializado do pipeline;
- versão do contrato;
- tentativa;
- resultado resumido;
- indicador de retomada e horários.

Cada run mantém `checkpoint_sequence`, incrementado sob bloqueio da linha do run.
O checkpoint possui `sequence` única por run e a retomada seleciona a maior
sequência. A chave idempotente também inclui `research_cycle` e `editor_cycle`,
distinguindo repetições legítimas de `researcher`, `research_gatekeeper`, `writer`
e `editor` de uma repetição da mesma conclusão.

Handoffs seguem o mesmo princípio. `handoff_sequence` é alocado sob bloqueio do
run, e cada `AgentHandoff` registra `sequence` e, quando aplicável,
`producer_agent_run_id`. A chave inclui os ciclos de pesquisa e edição. Assim, um
segundo pacote do pesquisador ou um novo feedback do editor é persistido como novo
handoff, enquanto a repetição da mesma entrega retorna o registro já existente.

O worker carrega o último checkpoint resumível. Redis não participa da fonte de
verdade da retomada. Falhas temporárias entram em `waiting_retry`, recebem backoff
limitado e são retomadas no mesmo run. Erros de contrato, validação ou invariantes
editoriais não são repetidos automaticamente.

Em uma reescrita retomada, o redator usa `draft` restaurado do checkpoint como
rascunho anterior. O executor não mantém uma cópia autoritativa em memória; o
estado persistido sempre prevalece após reinício do worker.

O Celery Beat consulta a cada minuto runs `queued` ou `waiting_retry`. Para
`waiting_retry`, `next_retry_at` deve existir e estar vencido; para `queued`, um
horário legado também é respeitado quando preenchido. A seleção usa
`FOR UPDATE SKIP LOCKED` e, na mesma transação, grava um token, o proprietário,
o horário, a expiração e a tentativa de despacho. O commit ocorre antes da
chamada ao broker, portanto processos concorrentes não publicam o mesmo run
durante uma reserva válida e nenhum lock PostgreSQL é mantido durante Redis.

Reservas ainda não confirmadas expiram em 120 segundos. Quando o broker aceita
a mensagem, a confirmação persistente registra o task ID e amplia a validade
para 15 minutos. Se nenhum worker adquirir o lease nesse período, o reaper marca
`dispatch.expired`; o próximo claim recebe token novo e registra
`dispatch.reclaimed`. Mensagens com token expirado, substituído, ausente ou já
consumido são confirmadas sem executar o pipeline.

Falhas do broker usam `dispatch_not_before` com backoff operacional próprio e
nunca alteram `next_retry_at`, `attempt`, status editorial ou a classificação do
erro do pipeline. Assim, Redis pode ficar indisponível sem perder o run durável.

O task `pipeline.run` não usa `autoretry_for` nem `self.retry`. PostgreSQL é a
fonte exclusiva do agendamento: o worker classifica a falha, persiste
`waiting_retry` e `next_retry_at`, libera o lease e confirma a mensagem atual. O
Beat é o único componente que cria uma nova entrega quando o horário vence. Isso
não se confunde com as tentativas HTTP curtas do gateway dentro de uma única
chamada ao provedor.

Antes do despacho, o Beat também executa o reaper de leases: runs `running` com
`lease_expires_at` vencido são bloqueados e revalidados, recebem
`error_code=worker.lease_expired` e transitam auditavelmente para `waiting_retry`
com `next_retry_at`. O lease morto é removido e um evento idempotente é criado. Se
o worker antigo reaparecer depois disso, a perda de propriedade do lease encerra
sua tarefa sem permitir que ela altere o estado do novo worker.

A reserva de despacho e o lease são estados distintos. O worker bloqueia o run,
valida o token e o relógio de retry e troca `claimed`/`sent` por `consumed` ao
adquirir o lease. A corrida entre reaper e worker é serializada pelo mesmo lock:
ou o worker consome o token atual, ou o reaper o substitui e a mensagem antiga é
rejeitada. A auditoria usa `dispatch.claimed`, `dispatch.sent`,
`dispatch.failed`, `dispatch.expired`, `dispatch.reclaimed` e
`worker.lease_acquired`, com chave idempotente derivada do token.

Os testes de concorrência e broker exigem serviços reais. A suíte completa deve
ser executada com `TEST_DATABASE_URL` e `TEST_REDIS_URL`; o cenário que inicia
processos reais de Celery também exige `RUN_CELERY_E2E=1`. Essas variáveis devem
apontar para instâncias isoladas, já migradas, de PostgreSQL e Redis.

## Identidade e compatibilidade dos eventos

Eventos novos de estágio carregam `stage_occurrence_id`, derivado de forma
determinística do run, estágio, ciclos de pesquisa e edição, tentativa do run e
tentativa lógica do estágio. A mesma emissão da mesma ocorrência reutiliza a
chave idempotente; outro ciclo ou retry produz uma ocorrência diferente.

A trilha inclui início, conclusão, falha, retry agendado, retomada, checkpoint e
handoff. `pipeline_events.sequence` continua sendo a ordem canônica por projeto,
alocada sob bloqueio da linha do projeto e protegida por constraint única. A
constraint `(pipeline_run_id, idempotency_key)` impede duplicação concorrente.

Eventos anteriores à migration `0008` são preservados sem backfill inferido. Eles
permanecem consultáveis com os novos campos de contexto nulos; `payload`, tipo,
estágio, sequência e horários mantêm o formato histórico.

## Versionamento

`ArticleBlock.id` é sempre físico e novo. `logical_block_id` acompanha o bloco
entre versões; `replaces_block_id` aponta para a revisão física anterior e
`revision_reason` registra reescrita dirigida ou preservação. Versões anteriores
não são atualizadas. A transação inteira é revertida se qualquer bloco, sentença
ou evidência falhar.

## Redis indisponível

- cache de contexto: recomposto do PostgreSQL;
- broker Celery: o run permanece `queued`, a falha de despacho é auditada e a API
  retorna o ID para retomada;
- descoberta editorial: não continua sem lock;
- fatos, eventos, versões, aprovações e checkpoints: nunca dependem exclusivamente
  do Redis.

## Observabilidade

Logs estruturados aceitam somente os campos `project_id`, `pipeline_run_id`,
`agent_role`, `stage`, `task_id`, `content_version_id`, `provider`, `model`,
`attempt` e `error_code`. Credenciais, prompts secretos e chaves não são incluídos.
Detalhes do run, checkpoints e transições estão disponíveis pela API de
`pipeline-runs`.

## Invariantes editoriais e orçamento de providers

O fluxo de produção é `planner -> researcher -> research_gatekeeper -> writer ->
editor -> finalizer -> quality_gate -> skill_curator -> human_review`. O curator
só executa quando a avaliação determinística foi aprovada. Uma saída inválida do
editor nunca é convertida em aprovação automática.

O writer faz uma geração integral por run. Revisões pagas ou determinísticas são
localizadas por `block_id`; blocos não solicitados permanecem byte a byte no
estado editorial. Sentenças factuais exigem referência a fatos aprovados.
Sentenças não factuais, headings e transições devem manter a lista de evidências
vazia.

Cada tentativa de provider LLM é registrada separadamente, inclusive resposta
truncada, JSON inválido, retry e fallback. Os totais cobrados são agregados no
`AgentRun` e no `PipelineRun`. Em produção, o pré-voo exige tarifas positivas de
entrada/saída para a rota primária e tarifas explícitas para qualquer fallback
distinto, impedindo que um custo zero torne o orçamento inoperante. Antes de cada
tentativa, o runtime estima o pior caso com o limite de tokens da rota e bloqueia
a chamada quando o teto do agente ou do pipeline seria ultrapassado. O registro guarda apenas diagnóstico seguro;
respostas e credenciais não são copiadas para a tabela de tentativas.

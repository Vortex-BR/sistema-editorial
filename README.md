# SEO Research Ledger Orchestrator

Sistema self-hosted de pesquisa e redação SEO com rastreabilidade por sentença. O redator só recebe fatos aprovados pelo auditor de pesquisa e o artigo final é bloqueado quando qualquer afirmação factual não possui evidência aprovada.

> **Atualização V3.7 — Release Hardening & Emergent Intelligence:** corrige a divergência de migration no CI, constrói/testa/publica a mesma imagem sem rebuild, adiciona auditorias de dependências, secrets, Trivy e SBOM, implementa rotação MultiFernet do cofre, restringe CORS sem quebrar `Idempotency-Key`, adiciona CSP em Report-Only e permite perguntas emergentes pós-pesquisa com validação determinística. A migration head permanece `0036`. Consulte `CHANGELOG_EDITORIAL_V3_7.md`, `IMPLEMENTATION_REPORT_V3_7.md`, `VALIDATION_EDITORIAL_V3_7.md` e `docs/EDITORIAL_V3_7_RELEASE_HARDENING.md`.

> **Base preservada:** a V3.6.3 continua responsável pela simplificação do briefing e correção do manifesto; a V3.6.2 mantém criação transacional, idempotência e retry durável; a V3.6.1 mantém a integridade pergunta → claim → frase → fonte.

## Publicar no EasyPanel

O `Dockerfile` da raiz é a variante de produção para um único serviço App do
EasyPanel. Ele serve o front-end na porta interna 8080 e encaminha `/api`
internamente para o FastAPI, além de iniciar o worker. PostgreSQL e Redis
continuam como serviços separados do mesmo projeto.

A imagem executa o entrypoint, migrations, Supervisor, API, Worker, Beat e Nginx
como o usuário sem login de UID/GID 10001. Somente `/var/lib/seo` é reservado
para estado temporário gravável (PIDs, cache temporário e agenda do Beat); código,
arquivos estáticos e skills permanecem sem permissão de escrita. Skills montadas
via Compose continuam explicitamente em modo somente leitura.

> **Regra permanente de réplicas:** o App de produção é all-in-one e inicia
> Nginx, API, Worker, Celery Beat e migrations no mesmo container. Configure
> **App replicas = 1** e mantenha exatamente uma réplica durante toda a operação,
> não apenas no deploy. Cada réplica adicional criaria outro Beat e outra execução
> concorrente de migrations. Escala horizontal futura exige separar API, Worker e
> Beat em serviços próprios; o Beat deve continuar com exatamente uma réplica.

> **pgvector é obrigatório.** A migration `0001` executa
> `CREATE EXTENSION IF NOT EXISTS vector`, migrations posteriores criam colunas
> `VECTOR` e o Compose/CI usam `pgvector/pgvector:0.8.5-pg17`. Uma imagem oficial
> `postgres:17` sem a extensão compilada não atende ao schema atual. PostgreSQL
> 17.10 com pgvector 0.8.5 é a combinação validada; a tag versionada
> `0.8.5-pg17` fixa a extensão. O scan de imagem continua obrigatório para
> acompanhar correções da distribuição. Não troque a major de um banco existente
> apenas alterando a tag da imagem.

1. Crie um serviço PostgreSQL persistente chamado `seo-postgres` usando a imagem
   `pgvector/pgvector:0.8.5-pg17`, com usuário, senha e banco próprios.
2. Crie um serviço Redis chamado `seo-redis` no mesmo projeto.
3. No serviço App, selecione a fonte **Docker Image** e use exclusivamente a
   imagem imutável publicada pelo workflow depois do smoke test:
   `ghcr.io/Vortex-BR/seo-docker:sha-SHA_COMPLETO`. Não configure a branch
   `main` como fonte de build de produção e não use `latest`: esse caminho
   recompila sem os metadados imutáveis comprovados pelo CI.
4. Em Domains, configure seu domínio com proxy port `8080` e marque-o como domínio
   principal.
5. Use os hosts internos exibidos pelo EasyPanel nas credenciais dos serviços.
   Não publique as portas 5432 ou 6379 na internet.
6. Defina as variáveis abaixo e faça novo deploy:

```env
DATABASE_URL=postgresql+asyncpg://USUARIO:SENHA_URL_ENCODED@HOST_INTERNO_POSTGRES:5432/BANCO
REDIS_URL=redis://default:SENHA_REDIS@HOST_INTERNO_REDIS:6379/0
CREDENTIAL_MASTER_KEY=CHAVE_FERNET_VALIDA
CREDENTIAL_MASTER_KEYS=
APP_ENV=production
FRONTEND_ORIGIN=https://seo.example.com
ADMIN_API_TOKEN=TOKEN_LONGO_E_ALEATORIO
SUPERIOR_SKILLS_MODE=enforced
SUPERIOR_SKILLS_PATH=/app/skills/superior
MAX_PIPELINE_COST_USD=0.80
MAX_AGENT_COST_USD=0.40
PROVIDER_CONNECT_TIMEOUT_SECONDS=15
PROVIDER_READ_TIMEOUT_SECONDS=90
V3_MIN_CLAIMS_PER_METHOD=3
V3_MIN_STEPS_PER_METHOD=3
V3_WRITER_REPAIR_ATTEMPTS=1
V3_EMERGENT_QUESTIONS_ENABLED=true
V3_MAX_EMERGENT_QUESTIONS=6
V3_MAX_SEARCH_PROVIDER_REQUESTS=96
V3_MAX_SEARCH_PROVIDER_RETRIES=32
V3_MAX_SEARCH_ESTIMATED_CREDITS=96
V3_SOURCE_DISCOVERY_TIMEOUT_SECONDS=240
V3_MAX_SOURCE_FETCHES=64
V3_MAX_SOURCE_RECOVERY_ROUNDS=2
V3_MIN_CANDIDATE_RELEVANCE=0.18
AGENT_TASK_DATA_MAX_CHARACTERS=400000
CONTENT_SIMILARITY_WARNING_THRESHOLD=0.72
CONTENT_DUPLICATE_THRESHOLD=0.90
```

`APP_COMMIT_SHA`, `APP_BUILD_VERSION` e `APP_SOURCE_DIGEST` já são gravados na
imagem durante o CI. Não os sobrescreva no runtime do EasyPanel. O startup
rejeita qualquer sobrescrita divergente do arquivo `/app/build-info.json`.
Deixe também vazios os campos **Command** e **Arguments** do serviço para manter
o `ENTRYPOINT` da imagem.

Durante uma rotação, configure `CREDENTIAL_MASTER_KEYS=NOVA_CHAVE,CHAVE_ANTIGA` em App, Worker e Beat. A primeira chave cifra e todas as chaves decifram. Execute primeiro o dry-run do endpoint administrativo de rotação antes de remover a chave antiga. O formato legado `CREDENTIAL_MASTER_KEY` continua aceito.

Gere `CREDENTIAL_MASTER_KEY` com:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Gere `ADMIN_API_TOKEN` separadamente com um segredo forte, configure-o somente
nas variáveis do serviço App no EasyPanel e faça novo deploy:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Criação e execução de projetos, retomada de pipelines, gravação de credenciais,
alteração de rotas de modelos e as demais mutações administrativas exigem esse
segredo no header `X-Admin-Token`. A interface solicita o token apenas ao usar
uma dessas ações e o mantém somente em memória. Nunca o configure como variável
`VITE_*`, nunca o inclua no bundle e nunca o envie em query string.

As variáveis `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD` pertencem ao
serviço PostgreSQL. No serviço App, a variável que efetivamente conecta a API é
`DATABASE_URL`. Se usuário ou senha tiverem caracteres reservados, aplique URL
encoding antes de montar a URL.

No startup de produção, o entrypoint exige `ADMIN_API_TOKEN`, uma
`CREDENTIAL_MASTER_KEY` Fernet válida, `DATABASE_URL`, `REDIS_URL`,
`SUPERIOR_SKILLS_MODE=enforced` e metadados imutáveis de commit, build e árvore
Git gravados na imagem. Depois de
`alembic upgrade head`, o pré-voo local confirma as `ModelRoutes` dos papéis
exigidos pela versão selecionada (seis na V2 e nove na V3), as credenciais ativas dos providers realmente referenciados,
as tarifas de entrada e saída usadas pelo orçamento (inclusive tarifas próprias
para um fallback distinto) e as versões ativas e aprovadas do núcleo editorial.
Nenhum provider pago é chamado. Falhas mostram somente os nomes dos requisitos e impedem a readiness
de ficar pronta; valores secretos nunca são reproduzidos. A readiness também
exige exatamente uma instância ativa de Worker e uma de Beat; enquanto houver
sobreposição durante um deploy, novos runs não são iniciados. Prepare rotas,
credenciais e versões no banco antes de trocar `APP_ENV` para `production`.
Desenvolvimento e testes continuam aceitando os defaults locais. Como o Beat
permanece embutido, mantenha permanentemente `App replicas = 1`.

`GET /api/v1/health` é liveness público e confirma apenas que o processo HTTP
está vivo. `GET /api/v1/readiness` é o gate operacional para receber tráfego e
trabalho: valida PostgreSQL, head Alembic, pgvector, Redis, broker, pré-voo,
modo enforced em produção e heartbeats recentes de Worker e Beat. Os
heartbeats ficam no Redis com TTL; não usam polling frequente no PostgreSQL. A
resposta expõe somente nomes genéricos de componentes e estados seguros, sem
URLs, hosts, tokens ou credenciais.

O procedimento completo de instalação, validação, backup, upgrade e correção de
collation está em [deploy/easypanel/README.md](deploy/easypanel/README.md).

## Editorial Intelligence V3 — pipeline executável e opt-in

A V3 está implementada como uma arquitetura paralela e executável. A versão 3.6.1
acrescenta um estado editorial canônico entre o contrato, a pesquisa e a redação. Ela
constrói uma hierarquia editorial tipada, cria uma intenção factual antes da pesquisa,
seleciona mercados e idiomas por tarefa, classifica fontes por relevância, conteúdo e
independência, recupera lacunas de forma dirigida e só então redige e executa três
revisões especializadas. Guias explicativos, comparações,
troubleshooting e educação comercial não são forçados ao molde procedural.

A V2 permanece preservada. A V3 exige a migration `0036`, projeto com
`editorial_pipeline_version=v3` e ativação explícita:

```env
EDITORIAL_PIPELINE_V3_ENABLED=true
EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED=true
```

Páginas transacionais de e-commerce e marketplaces são rejeitadas. Blog de loja
ou fabricante é `comparison_only`: não conta para autoridade ou diversidade,
não sustenta afirmação absoluta e não pode ser referência externa recomendada.
Guias técnicos independentes são úteis para o procedimento, mas permanecem
corroboradores; vocabulário científico isolado não promove um site independente
a autoridade acadêmica.

O pipeline inclui leitura estruturada de HTML/PDF, claims contextualizados,
triangulação, alocação round-robin por nó, uma passagem suplementar realmente
executável e orientada por lacunas, dossiês por nó,
editor de desenvolvimento, fact-checker, editor de linguagem, rubrica mínima de
85% e revisão humana obrigatória. Inventário de abordagens, matriz de decisão e
referências por abordagem são exigidos somente quando o briefing escolhe um guia
procedural. A dimensão das alternativas é declarada no briefing e validada pelo
Knowledge Architect antes da pesquisa. Todos os tipos passam por gates de cobertura, ordem, dependências e
profundidade proporcional.


### Motor de Inteligência Editorial — núcleo V1

O núcleo V1 cria um `ContentIntelligenceState` por execução e registra snapshots
imutáveis nas transições relevantes. Esse estado contém objetivo, intenção, leitor,
restrições, perguntas canônicas, responsabilidade de cada seção, grafo de fontes e
claims, conflitos, lacunas e políticas de escrita.

O fluxo V3.6.1 mantém os estágios de inteligência e adiciona recuperação orientada por lacunas:

```text
knowledge_gate
→ intelligence_planner
→ research_planner orientado pelo mapa de perguntas
...
→ knowledge_synthesizer
→ evidence_graph_builder
→ intelligence_gate
→ targeted_source_recovery (quando recuperável)
→ novo evidence_graph_builder e intelligence_gate
→ knowledge_completeness_gate
→ writer
```

O Writer só é executado quando o estado está `writer_ready`. Frases verificáveis
sem claim, claims usados fora da seção autorizada, claims condicionais sem condição,
conflitos não resolvidos e lacunas essenciais bloqueiam a execução. O mesmo estado é
revalidado no Writer, após o editor de linguagem e no quality gate. Aprovação humana
continua obrigatória.

Consulte [docs/EDITORIAL_V3.md](docs/EDITORIAL_V3.md) e o
[runbook de produção](docs/EDITORIAL_V3_PRODUCTION_RUNBOOK.md). A arquitetura compartilhada está documentada em
[docs/UNIVERSAL_EDITORIAL_HIERARCHY.md](docs/UNIVERSAL_EDITORIAL_HIERARCHY.md).

A pesquisa V3.5 está detalhada em [docs/EDITORIAL_V3_5_RESEARCH.md](docs/EDITORIAL_V3_5_RESEARCH.md), com mudanças em [CHANGELOG_EDITORIAL_V3_5.md](CHANGELOG_EDITORIAL_V3_5.md), implementação em [IMPLEMENTATION_REPORT_V3_5.md](IMPLEMENTATION_REPORT_V3_5.md) e validação em [VALIDATION_EDITORIAL_V3_5.md](VALIDATION_EDITORIAL_V3_5.md).

## Executar

Para bancos locais novos, o ambiente usa PostgreSQL 17 com pgvector pela imagem
`pgvector/pgvector:0.8.5-pg17`; não substitua esse serviço por `postgres:17` puro. O
Compose usa o volume novo `postgres17_data` e não monta o antigo
`postgres_data`. Se houver um volume PG16 com dados, preserve-o e faça uma migração
planejada para o volume PG17; não renomeie o volume nem apenas troque sua tag.

O ambiente comprovado executou PostgreSQL 17.10, pgvector 0.8.5 e o head Alembic
real deste repositório, atualmente `0036`. Confirme sempre as versões efetivas
com `SELECT version()`, `pg_extension`, `alembic current` e `alembic heads`.

1. Copie `.env.example` para `.env` e defina `CREDENTIAL_MASTER_KEY`.
2. Execute `docker compose up --build`.
3. Abra `http://localhost:3000`.

Em desenvolvimento e teste, a documentação da API fica em
`http://localhost:8000/docs`. Em produção, `/docs`, `/redoc` e `/openapi.json`
não são registrados. Credenciais de provedores são cadastradas pela tela
Configuração e persistidas criptografadas; elas nunca são devolvidas pela API.

### Política de pesquisa orientada por intenção — V3.5

A palavra-chave SEO não é mais tratada como o objeto factual da pesquisa. O
Knowledge Architect grava uma intenção canônica com assunto, entidades, métodos,
idioma, país, jurisdição e tipo editorial. O planejador usa essa estrutura para
montar consultas naturais e limitadas, sem transformar listas de palavras-chave
em frases artificiais.

Os mercados são escolhidos por tarefa. Conteúdo `pt-BR` começa pelo mercado
`BR/pt`; evidências científicas, mecanismos, riscos, comparações e referências
podem ampliar para `US/en`, `ES/es` e `CH/de`. Uma jurisdição mencionada tem
prioridade. A V3.5 não adiciona `-site:.br` globalmente e não exclui o Brasil por
padrão. Cada consulta enviada a um mercado estrangeiro é localizada para o idioma
correspondente, preservando nomes próprios, marcas, siglas e termos científicos
não traduzíveis.

A descoberta não para ao encontrar dois links quaisquer. O gate considera
relevância temática, domínios independentes, autoridade e papéis de fonte
requeridos por cada nó. Tavily e Serper compartilham limites de consultas lógicas,
requisições reais, retries, créditos estimados e tempo. Erros permanentes abrem o
circuito do provedor; rate limit e falhas transitórias abrem circuito temporário.
Depois da leitura estruturada, a cobertura é validada novamente por tarefa. Em
caso de lacuna, o sistema gera consultas de recuperação a partir do motivo real da
rejeição e só bloqueia ao esgotar os ciclos e orçamentos configurados.

Os parâmetros de `ModelRoute` usam uma allowlist por provider. A forma canônica
aceita `temperature`, `max_output_tokens`, `timeout_seconds`, `max_retries`,
`response_format`, custo por milhão e, somente para modelos OpenAI de raciocínio,
`reasoning_effort`. Aliases de limite de tokens são normalizados antes da
persistência. Headers, URLs, credenciais, ferramentas, callbacks, proxies,
arquivos, chaves desconhecidas e combinações incompatíveis são recusados com
erro seguro antes de criar o manifesto ou chamar um provider.

Ao salvar a primeira credencial OpenAI, Anthropic ou Gemini, o backend cria
somente as rotas editoriais ainda ausentes. Todas nascem com limites de tokens,
timeout, retries e tarifas positivas de entrada/saída, de modo que o preflight de
produção e o orçamento não sejam contornados por uma rota automática sem preço.
Os defaults atuais usam `claude-sonnet-5` e `gemini-3.5-flash`; revise as tarifas
no painel quando o provedor alterar preços ou quando a conta usar Batch, Flex ou
Priority em vez do modo Standard.

## Fluxo editorial humanizado e controle de custo

O pipeline separa arquitetura editorial, pesquisa, redação, revisão substantiva,
finalização e quality gate. Antes da pesquisa, o sistema cria um contrato universal
com nós ordenados, dependências, aplicabilidade e peso de profundidade. O planejador
pode produzir de três a dezesseis perguntas vinculadas a esses nós e um blueprint
narrativo igualmente rastreável. O writer recebe somente fatos
aprovados e faz uma única geração integral. Correções posteriores substituem
apenas os blocos identificados pelo editor, preservando IDs, posições e o restante
do artigo.

Somente sentenças factuais exigem evidência. Títulos, subtítulos e transições
editoriais devem ser marcados como não factuais e manter `evidence=[]`; isso
permite progressão natural sem transformar cada frase em paráfrase do ledger. O
editor devolve os blocos realmente revisados, e uma saída inválida do provider é
bloqueada em vez de ser aprovada por fallback.

Cada tentativa LLM — sucesso, erro, truncamento, JSON inválido, retry e fallback —
é persistida em `provider_attempts` com tokens, custo estimado, latência e
diagnóstico seguro. `MAX_PIPELINE_COST_USD` e `MAX_AGENT_COST_USD` interrompem a
próxima chamada antes de ultrapassar o orçamento projetado. Esses limites cobrem
as chamadas LLM do gateway; custos independentes do provedor de busca ou de
embeddings devem ser controlados também por quotas próprias da conta.

O briefing aceita faixa de palavras, estrutura mínima, seções obrigatórias, voz
da marca, exemplos aprovados, objetivo comercial, CTA, fontes preferidas ou
proibidas, idade máxima das fontes e claims a evitar. Quando esses campos não são
informados, os defaults globais permanecem como proteção, não como molde rígido.

Na V3, apenas guias procedurais exigem os métodos nomeados no briefing. O sistema
cria pesquisa específica por método, exige claims e passos mínimos, bloqueia a
redação antes de gastar com o Writer quando o conhecimento está incompleto e faz
no máximo uma reparação estrutural dirigida. Um artigo com blocker crítico tem o
score visual limitado a 59%, para que a média não masque uma peça não publicável.

## Skill-superior e memória dos agentes

Na inicialização, o sistema registra um núcleo global e uma persona versionada
para cada agente LLM a partir de `skills/superior`. O PostgreSQL é a fonte
durável de identidade, handoffs, memórias e padrões editoriais; o Redis guarda
somente cache, locks e estado transitório.

Use `SUPERIOR_SKILLS_MODE=shadow` para auditar o contexto compilado sem alterar
o prompt enviado ao provider. Após validar as versões ativas e o preview
administrativo, configure manualmente `SUPERIOR_SKILLS_MODE=enforced` no serviço
App do EasyPanel e faça novo deploy. Nesse modo, ausência do núcleo global ou da
persona ativa falha fechada em vez de voltar silenciosamente para shadow. Para
rollback, restaure `SUPERIOR_SKILLS_MODE=shadow` e faça novo deploy. As mutações em
`/api/v1/admin`, `POST /api/v1/projects`, as rotas de iniciar ou retomar
execuções e os `PUT` de credenciais/modelos exigem o header `X-Admin-Token`
correspondente a `ADMIN_API_TOKEN`. A documentação interativa da API lista os
endpoints de versionamento, ativação, memória, fontes, padrões, descoberta e
embeddings.

O preview administrativo de contexto usa exclusivamente
`POST /api/v1/admin/agent-context/preview`, protegido por `X-Admin-Token`. Papel,
projeto, run opcional e texto da tarefa são enviados em um corpo JSON; conteúdo
editorial nunca é aceito em path ou query string. O preview apenas compõe dados
locais persistidos, desabilita embeddings externos e não chama nenhum LLM.

Skills aprendidas iniciam desativadas como `candidate`. Repetições equivalentes
são deduplicadas por fingerprint e só chegam a `corroborated` depois de, por
padrão, três runs positivos distribuídos em pelo menos dois artigos. Esses limites
podem ser elevados por `LEARNED_SKILL_STABILITY_THRESHOLD` e
`LEARNED_SKILL_MIN_INDEPENDENT_ARTICLES`, mas nunca reduzidos abaixo de 3 e 2.
Revisão humana, promoção para `stable` e autorização de ativação são passos
administrativos separados. `rollback` desativa imediatamente a injeção futura e
preserva a trilha de evidências e decisões.

Depois do editor automático e do finalizador, o run entra em
`needs_human_approval`. O pacote de revisão reúne artigo, fatos, fontes,
cobertura, conflitos, SEO, mudanças e riscos. Somente uma ação administrativa
com identidade explícita do editor-chefe pode aprovar, rejeitar ou solicitar uma
nova revisão. Aprovação libera a exportação publicável; antes disso, apenas um ZIP
marcado como `RASCUNHO / NÃO PUBLICAR` pode ser gerado. Solicitar revisão cria um
novo run e uma nova `ArticleVersion`, preservando o histórico anterior.

Cada novo pipeline run também recebe um `ExecutionManifest` imutável antes de
ser enfileirado. Ele fixa checksums/versões de super-skills, skills padrão e
aprendidas, rotas e parâmetros de modelo, contratos, memórias, padrões,
embedding, provedor de busca, feature flags e identidade do build. Checkpoints e
retomadas reutilizam esse mesmo manifesto; alterações administrativas passam a
valer apenas para runs novos. Drift, dependência ausente ou conteúdo com padrão
de segredo fazem a execução falhar explicitamente. Commit, versão e digest da
árvore Git vêm do arquivo imutável gravado na imagem, e variáveis divergentes no
runtime são recusadas.
O painel e o ZIP editorial mostram somente um resumo seguro, acrescido dos IDs
run-scoped de handoffs e snapshots já produzidos.

Antes do gate de editor-chefe, uma avaliação independente aplica a rubrica
versionada `quality-rubric.v5`. O gate roda **antes** do `skill_curator`: conteúdo
reprovado não alimenta skills nem memória editorial. Cobertura de perguntas
centrais, citações nos snapshots, números, entidades, escopo, negação, causalidade,
conflitos, duplicação e aderência ao briefing são recalculados sem confiar no
`entailment_score` declarado pelo writer. A sobreposição lexical é apenas um
sinal diagnóstico; ela não decide sozinha se uma frase é sustentada.

A rubrica também bloqueia sinais de redação mecânica: perguntas internas usadas
como subtítulo, meta-narração sobre o próprio artigo, aberturas repetidas, headings
longos, linguagem genérica, seções rasas e atribuições de fonte visíveis no texto.
Os limites de palavras e estrutura vêm do briefing do projeto quando informados.
Blockers críticos não são compensados por SEO ou por média alta. O score nunca
publica automaticamente; a aprovação humana continua obrigatória.

Cada fato referencia um `SourceSnapshot` imutável do próprio run. O snapshot
preserva os metadados editoriais aplicados na captura — incluindo URL canônica,
domínio, título, autoria/publicação, tipo, confiabilidade, hash, timestamp e
método de extração. O registro `Source` continua agregando a identidade da URL,
mas alterações posteriores nele não mudam fatos, revisão humana ou ZIPs antigos.

Artigos aprovados também permanecem no PostgreSQL com Markdown, HTML, versões,
metadados e uma impressão semântica. Antes da pesquisa, um novo projeto é
comparado com esse histórico: quase-duplicatas vão para revisão humana e temas
parecidos chegam ao redator com um alerta para construir um ângulo diferente.

## Quality gate da imagem de produção

O GitHub Actions executa `image-smoke` no Linux depois dos gates de backend,
frontend, integração e build. O job reconstrói uma tag local vinculada ao SHA,
usa PostgreSQL 17 com pgvector e Redis descartáveis, comprova falha fechada com
configuração inválida e valida migrations, liveness, readiness, autenticação,
documentação desabilitada, processos estáveis e execução não root. A auditoria
também recusa segredos e resíduos de desenvolvimento na imagem final. Em pushes
para `main`, somente depois desses gates a imagem é publicada no GHCR com a tag
imutável `sha-<SHA completo>`; o cleanup remove somente os recursos nomeados pelo
run. O EasyPanel deve implantar essa tag ou o digest correspondente.

Em pull requests, `CANDIDATE_SHA` é o SHA real da branch (`pull_request.head.sha`)
e alimenta o checkout, a tag local, `APP_COMMIT_SHA` e os labels OCI de revisão e
versão. `CI_MERGE_SHA` registra separadamente o merge sintético do GitHub e nunca
identifica a release candidata. O Dockerfile final copia por allowlist somente a
aplicação, Alembic e seus arquivos de runtime; o `.dockerignore` impede que testes,
fixtures, caches, ambientes locais e artefatos de desenvolvimento entrem no
contexto de build.

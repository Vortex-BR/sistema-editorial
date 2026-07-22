# Relatório de implementação — Editorial V3.6.2

## 1. Problemas reproduzidos

### Criação V3 com início automático

O fluxo anterior podia persistir o projeto antes de conseguir construir o
`ExecutionManifest`. Quando uma rota, skill ou credencial não podia ser fixada,
a interface recebia apenas `Execution dependencies could not be fixed safely` e
o usuário podia terminar com um projeto sem run.

### Projeto V2 sem evolução

A validação operacional usava o conjunto global de papéis editoriais. Assim,
dependências exclusivas da V3 podiam interferir na criação ou no início de um
projeto V2. Projetos já criados sem run também não tinham uma ação clara de
recuperação na interface.

## 2. Arquitetura implementada

### 2.1 Preflight determinístico

`execution_preflight.py` inspeciona, por versão:

- flags V3;
- ModelRoutes necessárias;
- validade e custo das rotas;
- credenciais LLM ativas e verificadas;
- pelo menos uma credencial Tavily ou Serper verificada;
- Superior Skill global e skill de cada papel;
- skills padrão;
- skills específicas V3.

O relatório usa códigos seguros, por exemplo:

```text
model_route:fact_checker
credential:search:unverified
super_skill:language_editor
skills:v3
feature_flag:EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED
```

### 2.2 Reparação segura das rotas

`model_route_bootstrap.py` cria somente papéis ausentes. A seleção de provedor
prioriza uma credencial verificada e respeita limites de token, timeout, retries
e custo. Rotas existentes não são substituídas. Configurações inválidas são
reportadas para correção administrativa.

### 2.3 Transação de criação

O endpoint `POST /projects` agora segue:

```text
pré-voo
→ validação do perfil e briefing
→ criação do Project
→ evento project.created
→ criação do PipelineRun
→ criação e validação imediata do ExecutionManifest
→ commit único
→ publicação durável no broker
```

Qualquer falha até o manifesto provoca rollback. Uma falha de publicação após o
commit fica registrada no ledger de dispatch e recebe retry do Beat.

### 2.4 Idempotência de ponta a ponta

O frontend gera uma chave por fingerprint do payload e a reutiliza em uma nova
tentativa quando a resposta da primeira requisição é perdida. O backend:

- retorna o projeto/run já criado para a mesma chave e mesmo payload;
- rejeita a chave se o payload mudou;
- cria o run faltante quando encontra um projeto legado idempotente sem run.

### 2.5 Separação V2/V3

Papéis V2:

```text
planner
researcher
research_gatekeeper
writer
editor
skill_curator
```

Papéis adicionais V3:

```text
development_editor
fact_checker
language_editor
```

A readiness usada ao iniciar um projeto recebe explicitamente a versão do
pipeline. Isso impede dependências V3 de bloquearem a V2.

### 2.6 Recuperação de projetos antigos

A tela do pipeline detecta a ausência de `last_run` e oferece
**Iniciar execução**. O dashboard não chama esse estado de falha nem simula um
run inexistente.

## 3. Campanha MSB

A campanha foi implementada em `frontend/src/lib/campaignPresets.ts`. Ela é
estática, versionada com a aplicação e não depende de localStorage ou de dados
privados. O clique em **Aplicar campanha**:

1. preenche o briefing V3 completo;
2. mantém todos os campos editáveis;
3. procura o perfil Maconha Seeds Bank;
4. marca início imediato;
5. preserva as vinte perguntas e a estrutura editorial fornecidas.

## 4. Observabilidade

- `/api/v1/readiness` inclui `execution_dependencies`.
- `/api/v1/config/execution-preflight` permite diagnóstico por V2/V3.
- A API retorna códigos e dependências sem expor chaves.
- A criação retorna o ID do run e o estado da publicação.
- Dispatch indisponível é distinguido de falha de criação.

## 5. Compatibilidade

- Sem alteração de schema.
- Head Alembic: `0035`.
- Sem alteração do `pipeline_contract_version` V3.6.1.
- Runs com manifesto válido continuam retomáveis.
- Projetos antigos sem run podem ser iniciados pela interface.

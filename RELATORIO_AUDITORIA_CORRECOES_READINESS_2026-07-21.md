# Auditoria e correções — prontidão operacional e criação de runs

Data: 2026-07-21

## Escopo executado

Foi analisado o código enviado do backend FastAPI/Celery, frontend React, fluxo de criação de projetos, gate de prontidão, configuração de pipelines V2/V3 e testes automatizados. O erro observado na interface foi reproduzido por inspeção do fluxo: a API bloqueava corretamente a execução com `SYSTEM_NOT_READY`, enquanto a interface exibia “Saudável” de forma fixa e descartava os componentes que explicavam o bloqueio.

## Falhas confirmadas e corrigidas

1. **Falso estado saudável na interface**
   - O cabeçalho e a barra lateral exibiam estado verde estático, sem consultar a API.
   - Agora a interface consulta `/api/v1/readiness` ao abrir e a cada 30 segundos.
   - Estados exibidos: `Verificando`, `Operacional`, `Atenção` e `Indisponível`.

2. **Erro `SYSTEM_NOT_READY` sem diagnóstico útil**
   - A API já devolvia os componentes bloqueados, mas o frontend ignorava o campo `components`.
   - Agora mensagens mostram exemplos como `worker: ausente`, `agendador: heartbeat expirado`, `migrações: desatualizadas` e `dependências de execução: incompletas`.

3. **Formulário validava somente dependências editoriais**
   - Antes do POST, o formulário verificava rotas/credenciais/skills, mas não validava Worker, Beat, Redis, broker, migrations e pré-voo completo.
   - Agora o formulário consulta a readiness da versão selecionada antes de tentar iniciar o run e apresenta o bloqueio antes da criação.
   - Continua possível desmarcar o início automático e salvar somente o projeto.

4. **Readiness usava o singleton global de configuração**
   - Aplicações FastAPI criadas com outro objeto `Settings` podiam inicializar com uma configuração e avaliar o gate com outra.
   - O endpoint, o gate de início e as verificações de ativação da V3 agora usam `request.app.state.runtime_settings`, a mesma configuração usada no startup da aplicação.

5. **Diagnóstico não era específico ao pipeline escolhido**
   - O endpoint agora aceita `pipeline_version=v2` ou `pipeline_version=v3`.
   - O formulário consulta exatamente a versão selecionada pelo usuário.

6. **Cobertura de regressão insuficiente para esse cenário**
   - Foram adicionados testes para o status real do layout, tradução dos componentes de readiness, resposta HTTP 503 esperada, bloqueio de criação quando Worker/Beat não estão prontos e consistência das configurações de runtime.

## Validações executadas

- Backend: **1010 testes aprovados**, 40 ignorados por dependerem de serviços externos/integração.
- Frontend: **79 testes aprovados**.
- Ruff: aprovado.
- ESLint: aprovado.
- TypeScript + build Vite: aprovado.
- `npm audit`: nenhuma vulnerabilidade reportada no ambiente de teste.
- `pip-audit`: não pôde consultar o banco externo de vulnerabilidades porque o ambiente ficou sem resolução DNS para `pypi.org`; portanto, não há afirmação de auditoria CVE completa das dependências Python.

## Limitações da validação

O ambiente desta auditoria não possui Docker, PostgreSQL, Redis nem acesso ao EasyPanel. Por isso, não foi possível executar um deploy all-in-one real, validar heartbeats reais de Celery Worker/Beat ou testar credenciais externas dos providers. Esses itens continuam protegidos pelos testes de unidade e pelo endpoint de readiness, mas devem ser confirmados após o deploy.

## Verificação após o deploy

Consulte:

```text
GET /api/v1/readiness?pipeline_version=v3
```

Para aceitar novos runs, todos os componentes precisam retornar `status: ready`. Caso a V3 esteja desativada, o componente `execution_dependencies` ficará incompleto e o pré-voo administrativo indicará as flags ou dependências exatas.

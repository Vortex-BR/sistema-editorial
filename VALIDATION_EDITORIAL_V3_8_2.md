# Validação Editorial V3.8.2

Data: 2026-07-21

## Verificações concluídas

- `python -m compileall` em `backend/app`, `backend/tests` e migrations: aprovado.
- Alembic: um único head `0037`.
- Geração SQL offline da migration `0036 -> 0037`: aprovada.
- Testes do endpoint, persistência isolada, sanitização, readiness e autorização específica: **39 aprovados**, **147 não selecionados**.
- Testes de arquitetura do pipeline e executor Celery assíncrono: **31 aprovados**.
- Análise sintática TypeScript das três unidades modificadas com TypeScript 5.8.3: aprovada.
- Verificação de integridade do ZIP final: executada após o empacotamento.

## Cobertura de regressão adicionada

- Agregação das cinco origens de diagnóstico.
- Proteção de projeto/execução e limite de consulta.
- Redação de DSN, token e senha.
- `ON CONFLICT` por referência de correlação.
- Conversão segura de UUID, data, enum, decimal e bytes em metadados.
- Commit isolado do log técnico.
- Autorização administrativa do novo endpoint.
- Carregamento da nova aba no front-end e abertura do diagnóstico selecionado.

## Limitações deste ambiente

- A suíte completa não foi concluída neste ambiente porque dependências de infraestrutura foram substituídas por stubs locais e alguns testes de integração aguardaram recursos externos. Esses stubs não fazem parte do pacote.
- `npm ci`, Vitest, ESLint e o build completo do Vite não puderam ser executados localmente porque os pacotes do front-end não estavam disponíveis no cache e o ambiente não possuía acesso ao registry. O Dockerfile continuará executando `npm ci` e `npm run build` durante o build real.
- Não foi executado canário com PostgreSQL, Redis, Celery e EasyPanel reais.

## Teste de produção recomendado

1. Fazer deploy com somente uma réplica do App.
2. Confirmar que o startup aplicou a migration `0037`.
3. Abrir um projeto e selecionar uma execução.
4. Abrir **Logs de erros** e confirmar resposta HTTP 200.
5. Executar uma pesquisa canário.
6. Em caso de falha, confirmar a presença da referência de correlação e que credenciais não aparecem no JSON exportado.

# Changelog Editorial V3.8.2

## Diagnóstico técnico no front-end

- Nova aba **Logs de erros** na página de cada projeto.
- Consulta limitada à execução selecionada, com atualização manual e atualização automática a cada 15 segundos durante uma execução ativa.
- Resumo por gravidade, busca textual e filtros por gravidade e origem.
- Exibição expansível de etapa, código, categoria, referência de correlação, provedor, modelo, HTTP, tentativa, política de retry, SQL sanitizado, traceback sanitizado e metadados.
- Cópia de um registro, cópia do resultado filtrado e exportação em JSON.
- Atalho **Abrir logs técnicos** no aviso de falha do projeto.

## Persistência e API

- Nova tabela `technical_error_logs`, criada pela migration `0037`.
- Novo endpoint administrativo `GET /api/v1/projects/{project_id}/error-logs`.
- Agregação de falhas persistidas, estado do pipeline, execuções de agentes, tentativas de provedores e eventos operacionais.
- Persistência idempotente por `correlation_id` com `ON CONFLICT DO NOTHING`.
- Gravação em transação independente para preservar o diagnóstico mesmo se a transação que registra o estado da falha não puder ser concluída.
- Exclusão em cascata junto ao projeto/execução e referência opcional ao agente.

## Segurança

- Endpoint protegido pelo token administrativo existente.
- Redação de credenciais em URL, DSN, cabeçalhos, parâmetros SQL e textos com `token`, `password`, `api_key`, `secret`, `DATABASE_URL` ou `REDIS_URL`.
- Parâmetros SQL são removidos; literais de SQL e traceback são sanitizados.
- Metadados são convertidos para JSON seguro, limitados em profundidade e não armazenam bytes brutos.
- Prompts e respostas completas dos provedores não são devolvidos pelo endpoint.

## Compatibilidade operacional

- Alembic head atualizado de `0036` para `0037`.
- O entrypoint de produção continua aplicando migrations antes de iniciar API, Worker, Beat e Nginx.
- Nenhuma limpeza de banco é necessária.
- Falhas antigas continuam visíveis pelas tabelas operacionais existentes; traceback completo e SQL sanitizado passam a ser persistidos para novas falhas após o deploy.

# Validação — Editorial V3.6

Data: 20/07/2026

## Resultado local

### Backend

- Ruff em `app` e `tests`: aprovado.
- `compileall` em aplicação, migrations e testes: aprovado.
- 944 testes coletados.
- 904 testes aprovados.
- 40 testes ignorados por pré-condições de infraestrutura externa.
- Nenhuma falha nos grupos executados.

Os testes foram executados em grupos para evitar que o limite do executor local
interrompesse a suíte longa. A soma cobre todos os arquivos coletados.

### Frontend

- Vitest: 67 testes aprovados em 8 arquivos.
- ESLint: aprovado.
- TypeScript e build Vite: aprovados.

### Testes específicos adicionados

- criação do estado canônico e checksum;
- fechamento das referências entre perguntas, seções, claims e fontes;
- gate de Writer com e sem evidência;
- bloqueio de claim usado na seção errada;
- bloqueio de claim condicional sem condição;
- promoção de frase factual marcada incorretamente como editorial;
- integração do mapa de perguntas com o plano de pesquisa;
- transições dos três novos estágios no grafo;
- compatibilidade do hook de transição com nós opcionais;
- agregação de múltiplas fontes corroborando o mesmo claim canônico;
- proibição de registros duplicados com metadados editoriais incompatíveis.

## O que não foi validado neste ambiente

- migration real em uma instância PostgreSQL 17 + pgvector com dados de produção;
- execução distribuída com Redis, Worker, Beat, leases e retomada após falha;
- chamadas reais a OpenAI, Anthropic, Gemini, Tavily ou Serper;
- qualidade editorial em múltiplos nichos avaliada por revisores humanos;
- carga, custo e latência com projetos grandes;
- rollback da migration em cópia real do banco.

Os testes marcados para PostgreSQL/Redis/broker foram ignorados quando o serviço
externo exigido não estava disponível. Isso não deve ser interpretado como
validação de staging.

## Checklist obrigatório de staging

1. Fazer backup do banco.
2. Aplicar `alembic upgrade head` e confirmar somente `0034` em `alembic heads`.
3. Verificar a criação de `editorial_intelligence_snapshots` e seus índices.
4. Criar um run novo V3.6; não retomar run V3.5.1.
5. Confirmar snapshots nos seis estágios documentados.
6. Inspecionar se perguntas do briefing aparecem nas tarefas de pesquisa.
7. Forçar uma seção sem evidência e confirmar bloqueio antes do Writer.
8. Forçar claim condicional sem condição e confirmar bloqueio.
9. Alterar uma frase factual no Language Editor e confirmar novo fact-check.
10. Validar os cinco tipos editoriais suportados em pelo menos três idiomas.
11. Medir tokens, custo, tempo e taxa de reparo.
12. Submeter os artigos a revisão humana cega antes do rollout.

## Critério de liberação

A V3.6 deve ser liberada em canário somente quando a migration, o fluxo completo,
a persistência dos snapshots, os provedores reais e a revisão humana passarem no
ambiente de staging. A aprovação humana final permanece obrigatória em produção.

# Validação Editorial V3.9.1

Data: 2026-07-22

## Validações executadas

- `python -m compileall -q app tests/test_editorial_v3_research_recovery_v374.py`: aprovado.
- Testes direcionados de pesquisa, cobertura por informação e proteção do grafo:
  **48 aprovados**.
- Casos novos validados:
  - uma única fonte autoritativa permite chegar à extração sem eliminar a
    exigência final de corroboração;
  - ausência total de fonte retorna `V3_SOURCE_FETCH_EXHAUSTED`;
  - o grafo avança quando `synthesis_ready=true`;
  - uma URL já lida, ao ser associada a outra tarefa, conta como progresso e não
    consome rodadas em falso.

## Limitações do ambiente

A suíte completa não foi coletada porque o ambiente de análise não possui as
dependências de infraestrutura de produção `asyncpg`, Celery, Redis e pgvector.
Para os testes direcionados foram usados stubs somente nos imports de
infraestrutura, sem substituir as regras de cobertura, busca ou transição que
foram testadas.

O build do front-end não pôde ser executado porque `node_modules` não estava
presente e o cache offline do npm estava incompleto. A alteração de interface é
limitada à tipagem e à apresentação do novo campo booleano `synthesis_ready`.

## Canário recomendado no EasyPanel

1. Fazer deploy sem limpar PostgreSQL ou Redis.
2. Confirmar `alembic current` em `0037`.
3. Criar uma nova execução; não retomar o run já bloqueado.
4. Verificar o evento `v3.sources.coverage_evaluated`.
5. Quando a única pendência for diversidade por tarefa, confirmar
   `synthesis_ready: true` e avanço para `knowledge_synthesizer`.
6. Confirmar que a cobertura por informação continua recuperando requisitos
   críticos com menos de duas fontes independentes.

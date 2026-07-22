# Validação Editorial V3.8.3

Data: 2026-07-22

## Validações executadas

- `python -m compileall -q app tests`: aprovado.
- Testes direcionados de extração, recuperação, política de fontes, repositório e pipeline V3: **73 aprovados**.
- Novos testes V3.8.3:
  - recuperação de lote vazio por documento;
  - associação por URL original e canônica;
  - preservação da criticidade atômica;
  - reconciliação semântica segura de `support_group`;
  - fonte comercial comparativa não contaminando bundle factual elegível.

## Limitações do ambiente de validação

O ambiente local não possui os pacotes exatos de produção `asyncpg`, `celery`, `redis`, `pgvector` e `ruff`. Para os testes direcionados, foram usados stubs apenas nos imports de infraestrutura que não participam das regras testadas. A suíte completa não foi executada porque parte dela inicializa PostgreSQL/Redis/Celery durante a coleta.

O front-end não foi alterado nesta versão. Os arquivos da V3.8.2 foram preservados.

## Canário recomendado no EasyPanel

1. Fazer deploy sem limpar o PostgreSQL.
2. Confirmar `alembic current` em `0037`.
3. Criar uma nova execução, em vez de reaproveitar a execução bloqueada.
4. Verificar na aba de logs o evento `v3.claims.evaluated`.
5. Confirmar que `fatos coletados` passa de zero antes da criação dos dossiês.
6. Caso haja novo bloqueio, exportar os logs; agora eles indicarão a etapa exata de descarte.

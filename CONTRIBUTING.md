# Contribuindo com o SEO Research Ledger Orchestrator

## Regras que não podem ser quebradas

1. **Uma única migration head.** Antes de abrir PR, execute `python scripts/ci/resolve_alembic_head.py` e `cd backend && alembic heads`.
2. **Uma única instância do Celery Beat.** Nunca escale o serviço Beat horizontalmente.
3. **A imagem publicada deve ser a mesma imagem testada.** O workflow V3.7 constrói uma única imagem, executa Trivy, SBOM e smoke test e só então publica o mesmo image ID.
4. **Sem segredos no repositório.** Somente `.env.example` e `.env.easypanel.example` podem ser versionados.
5. **Nenhuma alteração de conteúdo herda aprovação anterior.** Mudanças no draft devem invalidar hash, revisão e fact-check.
6. **Skills e rotas são contratos versionados.** Alterações em schemas, skills ou papéis exigem testes de compatibilidade V2 e V3.
7. **Toda correção precisa de teste de regressão.** O teste deve reproduzir a falha antes da correção e validar o comportamento, não apenas a presença de campos.

## Preparação local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-dev.txt
cd frontend && npm ci && cd ..
```

## Validações obrigatórias

```bash
python -m ruff check backend scripts/ci/resolve_alembic_head.py
python -m pytest -q backend/tests \
  --ignore=backend/tests/test_celery_dispatch_e2e.py \
  --ignore=backend/tests/test_migrations_integration.py \
  --ignore=backend/tests/test_pipeline_dispatch_broker_integration.py \
  --ignore=backend/tests/test_pipeline_dispatch_integration.py \
  --ignore=backend/tests/test_pipeline_integration.py \
  --ignore=backend/tests/test_redis_integration.py

cd frontend
npm test
npm run lint
npm run build
```

## Integração local com Docker

```bash
docker compose up -d postgres redis
cd backend
DATABASE_URL=postgresql+asyncpg://seo:change-me@127.0.0.1:5432/seo_ledger \
REDIS_URL=redis://127.0.0.1:6379/0 \
python -m alembic upgrade head

python -m pytest -q \
  tests/test_pipeline_integration.py \
  tests/test_pipeline_dispatch_integration.py \
  tests/test_pipeline_dispatch_broker_integration.py \
  tests/test_migrations_integration.py \
  tests/test_redis_integration.py

RUN_CELERY_E2E=1 python -m pytest -q tests/test_celery_dispatch_e2e.py
```

## Migrations

- Use nomes sequenciais e descritivos.
- Escreva `upgrade` e `downgrade`.
- Atualize `backend/tests/test_migrations_integration.py` somente para o novo head real.
- Nunca deixe um número fixo de migration no workflow. O CI resolve o head pelo grafo Alembic.
- Teste upgrade em banco vazio e em cópia anonimizada do banco atual.

## Rotação da chave do cofre

A V3.7 aceita:

```env
CREDENTIAL_MASTER_KEYS=NOVA_CHAVE,CHAVE_ANTIGA
CREDENTIAL_MASTER_KEY=CHAVE_ANTIGA
```

A primeira chave cifra e todas decifram. Procedimento:

1. Adicione `NOVA_CHAVE,CHAVE_ANTIGA` em App, Worker e Beat.
2. Reinicie todos os processos.
3. Faça `POST /api/v1/config/credentials/rotate-master-key` com `{"dry_run": true}`.
4. Confirme o número de credenciais pendentes.
5. Execute com `{"dry_run": false, "confirmation": "ROTATE"}`.
6. Verifique novamente em dry-run; `pending_rotation` deve ser zero.
7. Remova a chave antiga de todos os serviços e reinicie.

Nunca troque a chave única diretamente enquanto existirem credenciais cifradas com ela.

## Pull requests

Cada PR deve conter:

- problema reproduzido;
- causa raiz;
- mudança mínima e compatível;
- testes antes/depois;
- impacto em migrations, ambiente e custo;
- atualização do changelog;
- plano de rollback.

Checks recomendados como obrigatórios na proteção da branch `main`:

- `backend-quality`;
- `frontend-quality`;
- `dependency-security`;
- `integration-tests`;
- `production-image`.

# Arquivos alterados — V3.9

## Backend

- `backend/app/schemas/editorial_v3_runtime.py`
- `backend/app/services/editorial_v3/research_planner.py`
- `backend/app/services/editorial_v3/information_coverage.py` — novo
- `backend/app/services/editorial_v3/artifact_repository.py`
- `backend/app/services/editorial_v3/content_intelligence.py`
- `backend/app/orchestration/v3/state.py`
- `backend/app/orchestration/v3/graph.py`
- `backend/app/orchestration/v3/executor.py`
- `backend/app/api/routes.py`
- `backend/app/core/config.py`
- `backend/app/services/execution_manifest.py`
- `backend/tests/test_editorial_v3_information_coverage_v390.py` — novo

## Front-end

- `frontend/src/pages/Pipeline.tsx`
- `frontend/src/pages/Pipeline.test.tsx`
- `frontend/src/styles.css`

## Configuração e documentação

- `.env.example`
- `.env.easypanel.example`
- `README.md`
- `CHANGELOG_EDITORIAL_V3_9.md`
- `RELATORIO_REFATORACAO_PESQUISA_V3_9.md`
- `VALIDATION_EDITORIAL_V3_9.md`
- `V3_9_CHANGED_FILES.md`

## Banco

Nenhuma migration nova. Alembic head: `0037`.

# Arquivos alterados na V3.5

## Backend — novos

- `backend/app/services/editorial_v3/research_intent.py`
- `backend/app/services/editorial_v3/search_runtime.py`
- `backend/app/services/editorial_v3/search_acceptance.py`
- `backend/tests/test_editorial_v3_research_v35.py`

## Backend — refatorados

- `backend/app/api/routes.py`
- `backend/app/core/config.py`
- `backend/app/orchestration/v3/executor.py`
- `backend/app/orchestration/v3/graph.py`
- `backend/app/orchestration/v3/state.py`
- `backend/app/schemas/api.py`
- `backend/app/services/agent_runtime.py`
- `backend/app/services/editorial_v3/document_parser.py`
- `backend/app/services/editorial_v3/knowledge_contract.py`
- `backend/app/services/editorial_v3/research_planner.py`
- `backend/app/services/editorial_v3/resilient_search.py`
- `backend/app/services/execution_manifest.py`
- `backend/app/services/research_engine.py`
- `backend/app/services/search_policy.py`

## Testes atualizados

- `backend/tests/test_editorial_v3_resilient_search.py`
- `backend/tests/test_editorial_v3_runtime_pipeline.py`
- `backend/tests/test_research_engine.py`
- `backend/tests/test_search_policy.py`

## Frontend

- `frontend/src/pages/Pipeline.tsx`

## Configuração e documentação

- `.env.example`
- `.env.easypanel.example`
- `.gitignore`
- `README.md`
- `V3_IMPLEMENTATION_STATUS.md`
- `CHANGELOG_EDITORIAL_V3_5.md`
- `IMPLEMENTATION_REPORT_V3_5.md`
- `VALIDATION_EDITORIAL_V3_5.md`
- `docs/EDITORIAL_V3.md`
- `docs/EDITORIAL_V3_PRODUCTION_RUNBOOK.md`
- `docs/EDITORIAL_V3_5_RESEARCH.md`
- `docs/ANALISE_CORRECAO_PIPELINE_EDITORIAL_V3.md`

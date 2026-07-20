# Arquivos alterados — Editorial V3.5.1 Generation Hardening

Total: **60 arquivos novos ou modificados**.

## Backend — aplicação

- `backend/app/api/routes.py`
- `backend/app/core/config.py`
- `backend/app/db/models.py`
- `backend/app/orchestration/v3/executor.py`
- `backend/app/orchestration/v3/state.py`
- `backend/app/schemas/api.py`
- `backend/app/schemas/editorial_hierarchy.py`
- `backend/app/schemas/editorial_v3.py`
- `backend/app/schemas/editorial_v3_runtime.py`
- `backend/app/services/agent_runtime.py`
- `backend/app/services/content_versioning.py`
- `backend/app/services/editorial_hierarchy.py`
- `backend/app/services/editorial_v3/artifact_repository.py`
- `backend/app/services/editorial_v3/content_similarity.py`
- `backend/app/services/editorial_v3/document_parser.py`
- `backend/app/services/editorial_v3/generation_context.py`
- `backend/app/services/editorial_v3/knowledge_completeness.py`
- `backend/app/services/editorial_v3/knowledge_contract.py`
- `backend/app/services/editorial_v3/language_quality.py`
- `backend/app/services/editorial_v3/procedural_quality.py`
- `backend/app/services/editorial_v3/research_planner.py`
- `backend/app/services/editorial_v3/text_integrity.py`
- `backend/app/services/editorial_v3/universal_quality.py`
- `backend/app/services/execution_manifest.py`
- `backend/app/services/model_catalog.py`
- `backend/app/services/readiness.py`
- `backend/app/services/superior_skills.py`

## Migration

- `backend/alembic/versions/0033_editorial_v3_structured_blocks.py`

## Backend — testes

- `backend/tests/test_editorial_v3_generation_hardening.py`
- `backend/tests/test_editorial_v3_research_hardening.py`
- `backend/tests/test_migrations_integration.py`
- `backend/tests/test_model_route_policy.py`
- `backend/tests/test_project_detail_consistency.py`
- `backend/tests/test_readiness.py`
- `backend/tests/test_research_retry_cache.py`
- `backend/tests/test_superior_context_enforcement.py`
- `backend/tests/test_superior_skills.py`

## Frontend

- `frontend/src/pages/AdminCuration.tsx`
- `frontend/src/pages/Config.tsx`
- `frontend/src/pages/NewProject.tsx`

## Skills

- `skills/default/editorial.language-quality.yaml`
- `skills/default/research.fact-extraction.yaml`
- `skills/default/writing.tone-and-style.yaml`
- `skills/superior/development-editor.yaml`
- `skills/superior/editor.yaml`
- `skills/superior/fact-checker.yaml`
- `skills/superior/language-editor.yaml`
- `skills/superior/writer.yaml`

## Documentação técnica

- `docs/ANALISE_ROBUSTA_GERACAO_CONTEUDO_V3_5.md`
- `docs/EDITORIAL_V3.md`
- `docs/EDITORIAL_V3_5_1_GENERATION_HARDENING.md`
- `docs/EDITORIAL_V3_PRODUCTION_RUNBOOK.md`

## Documentação/configuração da raiz

- `.env.easypanel.example`
- `.env.example`
- `CHANGELOG_EDITORIAL_V3_5_1.md`
- `IMPLEMENTATION_REPORT_V3_5_1.md`
- `README.md`
- `V3_5_1_CHANGED_FILES.md`
- `V3_IMPLEMENTATION_STATUS.md`
- `VALIDATION_EDITORIAL_V3_5_1.md`

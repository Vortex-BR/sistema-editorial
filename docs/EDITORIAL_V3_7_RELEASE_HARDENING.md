# Editorial V3.7 — Release Hardening e inteligência emergente

## Fluxo de publicação

```text
quality backend/frontend
→ auditoria de dependências e secrets
→ integração real Postgres/Redis/Celery
→ build único da imagem
→ Trivy + SBOM
→ smoke fail-closed
→ tag GHCR no mesmo image ID
→ push sem rebuild
```

## Fluxo de perguntas emergentes

```text
perguntas do contrato
→ pesquisa e síntese
→ claims, condições, limitações e conflitos
→ Planner propõe no máximo N perguntas emergentes
→ motor valida seção, duplicidade e vínculo com evidência
→ perguntas aceitas entram no Evidence Graph
→ gate e recuperação direcionada
```

O modelo não tem autoridade para aceitar a própria proposta. Perguntas sem evidência relacionada são descartadas.

## Variáveis novas

```env
CREDENTIAL_MASTER_KEYS=
V3_EMERGENT_QUESTIONS_ENABLED=true
V3_MAX_EMERGENT_QUESTIONS=6
```

## Migration

Permanece `0036`.

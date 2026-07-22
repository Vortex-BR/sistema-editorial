# Changelog — Editorial V3.7

## Integridade de CI e imagem

- O head Alembic deixou de ser fixado manualmente no workflow e agora é resolvido pelo grafo de migrations.
- A imagem de produção é construída uma única vez, analisada, testada e publicada sem rebuild.
- PostgreSQL/pgvector de CI foi fixado em `pgvector/pgvector:0.8.5-pg17`.
- Foram adicionados Trivy, SBOM CycloneDX, Gitleaks, pip-audit e npm audit.
- Dependabot foi configurado para Python, npm, GitHub Actions e Docker.
- Dependências Python foram separadas em runtime e desenvolvimento.

## Cofre de credenciais

- Suporte a `MultiFernet` com `CREDENTIAL_MASTER_KEYS`.
- Compatibilidade mantida com `CREDENTIAL_MASTER_KEY`.
- Rotação transacional com dry-run, verificação e confirmação explícita.
- Endpoint administrativo: `POST /api/v1/config/credentials/rotate-master-key`.

## Hardening HTTP

- CORS restrito aos métodos e headers realmente utilizados, incluindo `Idempotency-Key`.
- CSP adicionada em modo `Report-Only`.
- HSTS adicionado sem `includeSubDomains` automático.

## Inteligência editorial

- Perguntas emergentes podem ser propostas após a síntese das evidências.
- Propostas são limitadas, deduplicadas, validadas por seção e exigem alinhamento com claims já coletados.
- Perguntas emergentes não podem alterar o escopo nem bloquear o pipeline por simples autodeclaração do modelo.
- Alinhamento lexical foi fortalecido com âncoras, dimensões semânticas e penalização de termos genéricos.
- Casos adversariais de temperatura versus pigmentação e sobreposição genérica receberam testes de regressão.

## Processo

- Adicionados `CONTRIBUTING.md` e `SECURITY.md`.
- Migration head permanece `0036`; não há migration nova.

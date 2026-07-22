# Validação Editorial V3.7.2

## Executado localmente

- Backend sem serviços externos: **996 aprovados, 1 ignorado**.
- Testes diretamente afetados por manifesto, rotas de modelo, contexto superior e supply chain: **120 aprovados**.
- Testes de política CI/segurança: **7 aprovados**.
- Ruff: aprovado.
- Python `compileall`: aprovado.
- Workflow YAML: carregado com sucesso.
- `.gitleaks.toml`: TOML válido.
- Scripts shell alterados: `bash -n` aprovado.
- Resolvedor Alembic: `0036`.
- Nenhum arquivo `.env` real incluído.
- As únicas strings com formato de chave presentes na árvore atual ficam na allowlist exata do Gitleaks.

## Dependência corrigida

- `pypdf==5.4.0` → `pypdf==6.14.2`.

O código usa apenas a API estável `PdfReader(BytesIO(...))`, preservada na série 6.x.

## Validação que depende do GitHub Actions

- resolução online do `pip-audit`;
- execução do Gitleaks em contêiner contra o histórico real do repositório;
- `npm audit`;
- testes com PostgreSQL, pgvector, Redis e Celery;
- Trivy, SBOM, smoke da imagem e publicação no GHCR.

O workflow agora imprime os IDs das vulnerabilidades, versões de correção e os caminhos/regras do Gitleaks sem expor os valores detectados.

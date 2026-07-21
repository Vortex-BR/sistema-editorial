# Validação — Editorial V3.7

## Resultado consolidado

- Backend: **1.032 testes coletados**.
- Backend: **992 aprovados**.
- Backend: **40 ignorados** por exigirem PostgreSQL/pgvector, Redis, Celery ou broker externos.
- Frontend: **73 testes aprovados** em 9 arquivos.
- Ruff: aprovado.
- Python `compileall`: aprovado.
- ESLint: aprovado.
- TypeScript: aprovado.
- Build Vite: aprovado.
- `npm audit --omit=dev --audit-level=high`: **0 vulnerabilidades**.
- Workflow do GitHub Actions: YAML carregado e validado estaticamente.
- Alembic head resolvido dinamicamente: **0036**.
- `scripts/ci/image-smoke.sh`: sintaxe Bash aprovada.
- Busca por segredos de produção conhecidos e arquivos `.env` reais no pacote: nenhum resultado.

## Testes novos

- keyring MultiFernet, rotação e idempotência;
- rotação transacional e falha antes de mutação;
- CORS com `Idempotency-Key` e rejeição de header não autorizado;
- CSP Report-Only e HSTS;
- resolução dinâmica do Alembic head;
- imagem única para build, scan, smoke e push;
- separação de dependências Python de runtime e desenvolvimento;
- alinhamento semântico adversarial;
- perguntas emergentes limitadas, deduplicadas e fundamentadas;
- política de supply chain e cobertura do Dependabot.

## Integrações externas

Os 39 cenários que exigem serviços externos foram coletados e corretamente ignorados no ambiente local; outro teste condicional também foi ignorado, totalizando 40. O pacote inclui o job `integration-tests` para executá-los com PostgreSQL/pgvector, Redis e Celery no GitHub Actions.

## Verificações não executadas localmente

- build e execução Docker, porque o ambiente de validação não possui Docker;
- Trivy, Gitleaks e geração real do SBOM, porque os binários não estão disponíveis localmente;
- `pip-audit` contra a base online de vulnerabilidades, porque o ambiente não conseguiu resolver `pypi.org` durante a consulta;
- chamadas reais a OpenAI, Gemini, Tavily e Serper;
- canário completo V2/V3 no EasyPanel.

Esses controles foram adicionados ao CI e devem ser confirmados no primeiro workflow e no canário de staging antes da promoção para produção.

## Aviso de dependência

A suíte emitiu uma advertência de terceiros sobre a integração `starlette.testclient`/`httpx`. Não houve falha de teste. A atualização dessa dependência deve ser feita em PR próprio, com validação de compatibilidade, e não misturada ao hardening da V3.7.

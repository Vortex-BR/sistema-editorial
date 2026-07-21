# Validação — Editorial V3.6.3

## Backend

- 977 testes aprovados em três grupos.
- 40 testes ignorados por dependerem de infraestrutura externa.
- Ruff aprovado.
- `compileall` aprovado.
- Alembic: `0036 (head)`.

## Frontend

- 73 testes aprovados.
- ESLint aprovado.
- TypeScript aprovado.
- Build Vite aprovado.

## Regressões cobertas

- Metadados `credential_verification_*` não são classificados como segredo.
- Um `api_key` real continua bloqueado e seu valor não aparece no erro.
- O schema ativo não expõe `jurisdiction`.
- Contratos legados descartam `jurisdiction` antes da validação.
- A campanha MSB não contém jurisdição e respeita todos os limites do formulário.
- O assunto factual aceita o texto detalhado da campanha.

## Limites

Não foram utilizadas credenciais reais de IA/pesquisa nem banco de produção. O deploy precisa de um teste canário real após `alembic upgrade head` e reinício do App/Worker/Beat.

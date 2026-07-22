# Relatório de correção Editorial V3.7.2

## Falhas observadas no GitHub Actions

### Backend quality

O teste `test_ci_scans_dependencies_secrets_and_the_exact_image` ainda exigia a action `gitleaks/gitleaks-action@v2`. A V3.7.1 havia removido corretamente essa action, mas o teste antigo não foi atualizado, provocando uma falha artificial no backend.

### Dependency security

O resumo mostrou `pip-audit=1`, `npm-audit=0` e `gitleaks=1`.

A versão `pypdf==5.4.0` continha vulnerabilidades conhecidas e foi substituída por `pypdf==6.14.2`.

O Gitleaks executa em modo `git` e examina o histórico. Portanto, as strings sintéticas que já haviam sido commitadas continuavam detectáveis mesmo depois de corrigir os arquivos atuais. A política agora libera somente os três valores falsos exatos usados pelos testes históricos. Nenhum caminho, regra ou formato de segredo foi liberado.

## Alterações

- teste de supply chain sincronizado com a implementação real;
- `pypdf` atualizado;
- fixtures atuais sem valores contíguos com formato de segredo;
- allowlist histórica exata;
- caminho da configuração do Gitleaks explícito;
- diagnóstico de vulnerabilidades e quantidade de achados nos logs.

## Banco de dados

Nenhuma migration nova. Head esperado: `0036`.

## Validação local

- 996 testes backend aprovados e 1 ignorado, divididos em grupos para evitar timeout do executor local;
- 120 testes diretamente relacionados às áreas modificadas aprovados;
- 7 testes de política CI/segurança aprovados;
- Ruff, compileall, YAML, TOML e sintaxe shell aprovados;
- Alembic permanece em `0036`.

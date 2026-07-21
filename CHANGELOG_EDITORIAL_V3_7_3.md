# Changelog Editorial V3.7.3

## Correção do gate Gitleaks

- Classificados os dois achados restantes do GitHub Actions como falsos positivos verificáveis:
  - `generic-api-key` no exemplo vazio `CREDENTIAL_MASTER_KEYS=` do relatório V3.7;
  - `curl-auth-header` no teste que envia deliberadamente `X-Admin-Token` vazio para confirmar falha fechada.
- Removidos os dois formatos da árvore atual:
  - o relatório deixou de apresentar a variável como atribuição vazia;
  - o smoke test passou a montar o nome do header por variável.
- Adicionadas exceções históricas restritas por regra, caminho e conteúdo exato, com condição `AND`.
- Nenhuma pasta, commit, regra completa, prefixo ou formato genérico de segredo foi liberado.
- Adicionados testes de regressão que impedem ampliação acidental da allowlist e o retorno das duas linhas problemáticas.

## Banco de dados

Nenhuma migration nova. O head permanece `0036`.

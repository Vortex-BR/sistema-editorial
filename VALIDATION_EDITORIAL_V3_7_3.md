# Validação Editorial V3.7.3

## Verificações executadas

- `.gitleaks.toml` carregado com `tomllib`.
- As duas exceções históricas foram validadas contra os caminhos e linhas exatos reportados pelo GitHub.
- `bash -n scripts/ci/image-smoke.sh`: aprovado.
- Testes de política de supply chain, Gitleaks e image smoke: **14 aprovados**.
- Primeiro grupo amplo do backend: **271 aprovados e 1 ignorado**.
- Ruff: aprovado antes da alteração documental/configuracional final.
- A árvore atual não contém mais:
  - `CREDENTIAL_MASTER_KEYS=` no relatório V3.7;
  - o literal `--header 'X-Admin-Token:'` no smoke test.

## Observação

A suíte backend completa não terminou no executor local por timeout durante um grupo não relacionado às alterações. A validação definitiva do histórico Git e do contêiner Gitleaks ocorre no GitHub Actions, pois o ZIP não contém o histórico do repositório.

## Resultado esperado no GitHub

```text
backend-quality       Successful
frontend-quality      Successful
dependency-security   Successful
integration-tests     Executado
production-image      Executado
```

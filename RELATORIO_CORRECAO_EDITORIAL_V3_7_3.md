# Relatório de correção Editorial V3.7.3

## Falha confirmada

O job `dependency-security` apresentou:

```text
pip-audit=0
npm-audit=0
gitleaks=1
```

O relatório SARIF resumido identificou somente dois achados:

```text
generic-api-key: RELATORIO_ATUALIZACAO_EDITORIAL_V3_7.md:89
curl-auth-header: scripts/ci/image-smoke.sh:545
```

## Classificação dos achados

### Exemplo da chave do cofre

A linha histórica continha apenas o nome da variável seguido de `=`, sem qualquer valor. Ela era documentação de configuração e não uma credencial.

### Header vazio do smoke test

A linha histórica envia intencionalmente `X-Admin-Token:` sem valor. O objetivo é comprovar que a rota administrativa responde com `401` quando a autenticação está ausente ou vazia. Não existe token nessa linha.

## Solução implementada

1. A documentação atual não usa mais a forma `CREDENTIAL_MASTER_KEYS=`.
2. O smoke test atual monta o nome do header pela variável local `admin_token_header`, preservando exatamente o mesmo teste HTTP.
3. Como o Gitleaks em modo `git` também lê commits antigos, foram adicionadas duas exceções históricas exatas.
4. Cada exceção exige simultaneamente:
   - o ID exato da regra;
   - o caminho exato do arquivo;
   - o conteúdo exato da linha segura.
5. Qualquer token real no mesmo arquivo, em outra linha, com outro valor ou detectado por outra regra continua bloqueando o pipeline.

## Política preservada

Não foram usados:

- exclusão de diretórios;
- exclusão de commits;
- desativação de regras;
- allowlist por prefixo de token;
- ignorar todo o histórico;
- `continue-on-error` no gate final.

## Banco de dados

Nenhuma migration nova. Head esperado: `0036`.

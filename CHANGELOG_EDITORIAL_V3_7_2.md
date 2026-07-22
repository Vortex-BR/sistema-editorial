# Changelog Editorial V3.7.2

## Correções

- Corrigido o teste legado que ainda exigia `gitleaks/gitleaks-action@v2`, embora a V3.7.1 já utilizasse a CLI oficial em contêiner.
- Atualizado `pypdf` de `5.4.0` para `6.14.2`, removendo vulnerabilidades conhecidas da versão antiga.
- Corrigida a política do Gitleaks para o fato de que o modo `git` examina todo o histórico, e não somente a árvore atual.
- Os três valores sintéticos usados nos testes agora são construídos em tempo de execução para não parecerem segredos no código atual.
- A allowlist cobre somente os três valores sintéticos exatos presentes no histórico; não libera pastas, regras, prefixos nem padrões genéricos.
- O workflow passa explicitamente `--config /repo/.gitleaks.toml`.
- Adicionado resumo seguro de achados do `pip-audit` e do Gitleaks aos logs do job, sem exibir valores secretos.

## Banco de dados

Nenhuma migration nova. O head permanece `0036`.

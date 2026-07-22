# Changelog Editorial V3.7.1

## Correção do gate de segurança do GitHub Actions

- Substituído `gitleaks/gitleaks-action@v2`, que exige licença em repositórios pertencentes a organizações, pela CLI oficial do Gitleaks executada em contêiner.
- Imagem do Gitleaks fixada por versão e digest: `v8.30.1`.
- Preservado o scan completo do histórico Git.
- Adicionado relatório SARIF redigido aos artefatos do job.
- O gate agora informa separadamente os códigos de saída de `pip-audit`, `npm audit` e Gitleaks.
- Adicionada allowlist estrita somente para três valores sintéticos usados em testes de proteção de segredos.
- Nenhum diretório inteiro, regra de detecção ou classe de segredo foi desativado.
- Adicionados testes de regressão para impedir o retorno da ação licenciada e de allowlists amplas.

Não há migration nova. O contrato editorial permanece `editorial-v3.7` e o Alembic permanece em `0036`.

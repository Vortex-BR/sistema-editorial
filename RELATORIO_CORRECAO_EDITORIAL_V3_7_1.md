# Relatório de correção Editorial V3.7.1

## Incidente

O push para o GitHub foi concluído, mas o workflow bloqueou no job `dependency-security`. Como consequência, `integration-tests` e `production-image` foram pulados por dependência.

## Causa estrutural corrigida

A V3.7 utilizava `gitleaks/gitleaks-action@v2` somente com `GITHUB_TOKEN`. Essa action exige `GITLEAKS_LICENSE` quando o repositório pertence a uma organização. Além disso, o projeto possui três strings deliberadamente semelhantes a chaves OpenAI em testes de sanitização, o que poderia gerar falsos positivos sem uma política explícita.

## Correção

- uso da CLI oficial do Gitleaks, sem dependência da licença da action;
- imagem `ghcr.io/gitleaks/gitleaks:v8.30.1` fixada por digest;
- scan do histórico Git com valores redigidos no log;
- relatório SARIF anexado ao workflow;
- allowlist exata por arquivo + literal para os três fixtures sintéticos;
- gate final exige sucesso de Python, npm e Gitleaks;
- testes automatizados da política de segurança.

## Validação necessária no GitHub

Depois do novo push, o job `dependency-security` deverá mostrar individualmente:

1. `Audit Python runtime dependencies`;
2. `Audit frontend runtime dependencies`;
3. `Detect committed secrets`;
4. `Enforce dependency security policy`.

Se o gate ainda falhar, o log final mostrará qual código de saída não foi zero, e os artefatos `pip-audit.json`, `npm-audit.json` e `gitleaks.sarif` permitirão identificar a causa real sem expor o valor de qualquer segredo.

# Relatório de implementação — Editorial V3.7

## Objetivo

Fechar os riscos remanescentes identificados na revisão técnica da V3.6.3 sem reintroduzir jurisdição, bloqueios de tema ou limitações artificiais de conteúdo.

## Mudanças estruturais

### CI imutável

O workflow anterior construía a imagem em jobs diferentes. A V3.7 usa um único job de imagem: build local, Trivy, SBOM, smoke e publicação do mesmo image ID. O head Alembic é calculado por `scripts/ci/resolve_alembic_head.py`, eliminando a divergência histórica `0032 x 0036`.

### Keyring criptográfico

`CredentialVault` agora usa `MultiFernet`. A primeira chave é primária; as demais são somente de leitura/rotação. A rotina de rotação valida todas as credenciais antes de alterar qualquer linha e revalida o plaintext antes do commit.

### Perguntas emergentes

Depois da síntese, o Planner pode sugerir perguntas que surgiram das evidências. O motor aceita apenas perguntas vinculadas a seção existente, não duplicadas e com claim relacionado. A criticalidade proposta pelo modelo só é preservada quando o alinhamento mínimo forte é atingido.

### HTTP

A política CORS permite somente os métodos e headers necessários. CSP permanece em relatório para não quebrar a aplicação sem observação prévia. HSTS é emitido com escopo conservador.

## Compatibilidade

- V2 preservada.
- V3 preservada com dois novos flags opcionais.
- `CREDENTIAL_MASTER_KEY` continua funcionando.
- Sem alteração de schema; Alembic continua em `0036`.

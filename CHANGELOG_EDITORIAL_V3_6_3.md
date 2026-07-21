# Changelog — Editorial V3.6.3

## Corrigido

- Falso positivo `EXECUTION_MANIFEST_CONTAINS_SECRET` causado por chaves de metadados iniciadas por `credential_`.
- Diagnóstico do manifesto agora informa somente o caminho seguro do campo rejeitado.
- Campanha MSB deixou de exceder o limite do assunto factual.
- Limite do assunto factual alinhado entre frontend e backend em 1.000 caracteres.
- Retomada de contratos antigos remove silenciosamente o campo legado de jurisdição antes da validação estrita.

## Removido

- Campo **Jurisdição e conformidade** do formulário de novo conteúdo.
- Campo `jurisdiction` do schema de briefing, contrato editorial, intenção de pesquisa e política de mercados.
- Priorização de mercados baseada em texto de jurisdição.
- Instruções editoriais de conformidade legal incorporadas à campanha MSB.
- Coluna legada `content_knowledge_contracts.jurisdiction` por meio da migration `0036`.

## Preservado

- Bloqueio de segredos reais, URLs de banco/Redis e tokens no manifesto.
- Regras de veracidade factual, evidência, escopo editorial e prevenção de alucinações.
- Criação transacional, idempotência e retry durável da V3.6.2.
- Motor de Inteligência Editorial e integridade de fluxo da V3.6.1.

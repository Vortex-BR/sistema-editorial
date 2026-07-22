# Changelog Editorial V3.8.3

Data: 2026-07-22

## Correção principal

Corrigido o bloqueio em que a etapa de síntese encerrava a execução com:

- `V3_APPROVED_CLAIMS_INSUFFICIENT`;
- `0 fatos coletados`;
- `0 fatos aprovados`;
- cobertura de fontes marcada como concluída.

## Alterações

### Extração de claims

- A associação entre fonte e tarefa agora verifica tanto a URL original quanto a URL canônica.
- Um lote que retorna `claims=[]` passa a ser recuperado por documento, da mesma forma que já ocorria com lotes que lançavam `TypeError`.
- A recuperação isolada pode ser acionada para qualquer tarefa sem claim persistido ou sem cobertura aprovada, e não apenas para tarefas com exceção técnica.
- O prompt de extração exige um registro por fonte corroboradora e reutilização consistente do mesmo `support_group`.
- Uma tarefa crítica não transforma automaticamente todas as afirmações extraídas em claims críticos.

### Persistência e aprovação

- Cada descarte de claim agora possui contador diagnóstico: URL incompatível, citação não encontrada, papel de evidência não permitido, fonte rejeitada, Fact Ledger recusado e outros.
- Claims semanticamente equivalentes, no mesmo nó e papel de evidência, podem reutilizar deterministicamente um grupo já existente quando números, negação e sobreposição lexical são compatíveis.
- Registros de fontes `comparison_only`, `discovery_only` ou rejeitadas permanecem não aprovados, mas não contaminam um conjunto factual sustentado por fontes elegíveis.
- A aprovação informa quantos registros não elegíveis foram ignorados em cada grupo.

### Diagnóstico operacional

- Novo evento `v3.claims.evaluated` com contagem total, aprovada, grupos, políticas das fontes e motivos agregados de bloqueio.
- Eventos `pipeline.blocked` agora incluem os diagnósticos da extração e da aprovação.
- Novos códigos distinguem as causas:
  - `V3_CLAIM_EXTRACTION_EMPTY`: nenhuma afirmação chegou ao Fact Ledger;
  - `V3_CLAIM_APPROVAL_EMPTY`: houve coleta, mas nenhuma afirmação passou pela política;
  - `V3_APPROVED_CLAIMS_INSUFFICIENT`: existem claims aprovados, porém abaixo do mínimo.

## Banco de dados

Nenhuma migration nova. O Alembic head permanece `0037`.

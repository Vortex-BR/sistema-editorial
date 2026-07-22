# Changelog Editorial V3.9.1 — Continuidade da recuperação de fontes

Data: 2026-07-22

## Problema corrigido

Execuções podiam terminar em `targeted_source_recovery` com o código
`V3_SOURCE_DIVERSITY_INSUFFICIENT`, mesmo quando já existia ao menos uma fonte
válida, autorizada e compatível para cada tarefa. O gate preliminar por tarefa
bloqueava o fluxo antes de a V3.9 extrair os claims e validar a diversidade no
nível correto: cada informação obrigatória.

Também havia um falso cenário de “nenhum progresso” quando o provedor retornava
uma URL já lida, mas agora pertinente a outra tarefa. A associação era gravada,
porém não era contabilizada como recuperação; o grafo repetia a etapa até
esgotar as rodadas.

## Correções

- O relatório de cobertura por tarefa agora informa `synthesis_ready`.
- Quando todas as tarefas possuem ao menos uma fonte aceita, com autoridade e
  papel compatíveis, e a única pendência é diversidade, o pipeline segue para a
  síntese.
- A exigência final não foi reduzida: informações críticas continuam exigindo
  duas fontes independentes no gate de cobertura por informação.
- URLs já conhecidas que passam a atender uma nova tarefa contam como progresso
  de recuperação e fazem o pipeline reprocessar os vínculos.
- A recuperação de inteligência também reconhece esse reaproveitamento.
- Códigos de diagnóstico agora priorizam `V3_SOURCE_FETCH_EXHAUSTED` quando uma
  tarefa não possui nenhuma fonte legível, em vez de classificá-la genericamente
  como falta de diversidade.
- Códigos de bloqueio antigos são limpos quando o corpus se torna seguro para
  síntese.
- O front-end apresenta “Pronta para síntese” quando a diversidade restante será
  tratada pelo gate informação por informação.

## Banco de dados

Nenhuma migration nova. O Alembic head permanece `0037`.

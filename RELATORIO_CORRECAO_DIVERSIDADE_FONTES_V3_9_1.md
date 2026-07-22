# Relatório de correção — diversidade e recuperação de fontes V3.9.1

## Diagnóstico do erro informado

O bloqueio ocorreu em `targeted_source_recovery`. O código anterior considerava
progresso apenas quando surgia uma URL inédita. Se a busca devolvesse uma fonte
já lida que pudesse atender uma tarefa deficiente, o `source_task_map` era
atualizado, mas o contador de progresso permanecia em zero. O grafo repetia a
recuperação e, ao alcançar o limite de rodadas, encerrava o run.

Além disso, o gate `source_coverage_gate` exigia duas fontes por tarefa crítica
antes da extração de claims. Após a refatoração V3.9, essa decisão ficou cedo
demais: o sistema ainda não sabia quais informações da tarefa estavam realmente
cobertas. O gate posterior já realiza essa validação de forma mais precisa por
requisito e possui recuperação própria.

## Fluxo corrigido

1. O gate por tarefa continua bloqueando ausência de fonte, fonte rejeitada,
   autoridade ausente ou papel de evidência incompatível.
2. Se cada tarefa tem fonte válida e a única pendência é diversidade, o corpus é
   marcado como seguro para síntese.
3. Claims são extraídos e associados aos requisitos de informação.
4. O gate por informação exige duas fontes independentes para requisitos
   críticos.
5. Somente as informações realmente deficientes geram novas consultas.
6. Fontes já lidas podem ser reaproveitadas para novas tarefas ou requisitos sem
   serem descartadas como duplicatas inúteis.

## Resultado esperado

O erro `V3_SOURCE_DIVERSITY_INSUFFICIENT` deixa de interromper prematuramente
execuções que já possuem base factual suficiente para análise. A qualidade final
permanece protegida porque a diversidade continua obrigatória no nível de cada
informação crítica antes da redação.

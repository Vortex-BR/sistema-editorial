# Editorial V3.7.4 — Research Synthesis Recovery

## Invariantes

1. O Writer nunca executa com cobertura incompleta.
2. Uma capacidade de fonte pode ser satisfeita por papel equivalente ou mais forte, nunca por fonte comercial incompatível.
3. Uma fonte pode sustentar mais de um nó somente quando o papel de evidência, a capacidade e a relevância forem compatíveis.
4. Dois domínios independentes continuam obrigatórios para cada nó core.
5. Um documento defeituoso não invalida claims verificáveis de outros documentos.
6. Falha de extração não é convertida em sucesso: ela permanece registrada e pode resultar em bloqueio editorial específico.
7. Runs e manifestos anteriores permanecem imutáveis.

## Diagnósticos operacionais

- `cross_task_assignment_count`: número de associações adicionais fonte→tarefa.
- `cross_task_assignments`: amostra limitada com documento, tarefa, nó, score e papel.
- `claim_extraction_persisted_count`: claims persistidos na tentativa atual.
- `claim_extraction_failures`: tarefa, nó, fase e classe do erro.
- `claim_extraction_failed_task_ids`: tarefas que exigiram recuperação.

Os diagnósticos não incluem credenciais, texto bruto, prompts completos ou respostas integrais do provedor.

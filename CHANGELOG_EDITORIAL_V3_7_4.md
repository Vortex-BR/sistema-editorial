# Changelog Editorial V3.7.4

## Research Coverage & Knowledge Synthesis Recovery

### Corrigido

- O gate de cobertura deixou de exigir igualdade literal entre papéis de fonte equivalentes. Uma fonte `scientific_primary` pode satisfazer uma preferência por `scientific_review`; fontes `specialist_practical` e `technical_procedural` também são tratadas por capacidade editorial.
- Fontes estruturadas agora podem ser vinculadas, de forma determinística, a outros nós que realmente sustentam. O mecanismo considera papel de evidência permitido, compatibilidade do papel da fonte e relevância lexical ponderada.
- A exigência de duas fontes independentes foi alinhada ao contrato: permanece obrigatória para nós `core`; nós de apoio e referências externas exigem uma fonte elegível, salvo regra mais forte posterior.
- A síntese não inicia quando o relatório atual de cobertura continua incompleto.
- Um `TypeError` durante a extração em lote não apaga toda a etapa. O lote é isolado por documento, os claims válidos são preservados e o diagnóstico registra apenas tarefa, nó, fase e tipo do erro.
- Claims insuficientes encerram a execução com `V3_APPROVED_CLAIMS_INSUFFICIENT`, em vez de aparecerem como falha técnica genérica.
- O grafo preserva o código e o motivo específicos produzidos pelo sintetizador.

### Observabilidade

- Métricas novas: `cross_task_assignment_count`, `cross_task_assignments`, `claim_extraction_persisted_count`, `claim_extraction_failures` e `claim_extraction_failed_task_ids`.
- Nenhum valor de credencial, conteúdo bruto de fonte ou resposta do provedor é incluído nesses diagnósticos.

### Compatibilidade

- Nenhuma migration nova. Alembic permanece em `0036`.
- Nenhuma variável de ambiente nova.
- Nenhum campo de jurisdição ou restrição editorial foi reintroduzido.
- Runs antigos permanecem imutáveis; a correção é validada criando uma nova pesquisa.

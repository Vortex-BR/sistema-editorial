# Editorial V3.8 — geração incremental por unidade de informação

## Objetivo

A V3.8 reduz o risco de perder um artigo inteiro por timeout, resposta truncada, reinício do worker ou falha de provedor. Em vez de solicitar todo o conteúdo em uma única chamada, o Writer percorre o blueprint na ordem canônica e produz uma seção por vez.

## Fluxo

1. O contrato editorial define a ordem das seções e os limites de palavras.
2. O executor identifica quais seções já possuem payload válido no checkpoint.
3. Para cada seção pendente, monta um contexto restrito àquela unidade: objetivo, claims permitidos, evidências relevantes, métodos vinculados, faixa de palavras e trecho anterior necessário para continuidade.
4. O agente retorna `V3WriterSectionOutput` para exatamente uma seção.
5. O executor valida fronteiras estruturais, escopo, faixa de palavras, limites mínimo e máximo de blocos, evidência factual e orçamento. A alocação impede que as unidades possam ultrapassar o teto de 300 blocos do artigo completo.
6. Se a unidade for inválida, executa um reparo estritamente limitado à mesma seção; nada é salvo antes de ela passar.
7. A seção é salva em `writer_sections`, o ID entra em `writer_completed_section_ids` e um checkpoint exclusivo é persistido.
8. Depois da última seção, o executor monta o `V3WriterOutput` completo com ordem e IDs determinísticos.
9. A validação integral do rascunho, os editores e os gates finais continuam sendo executados normalmente.

## Retomada

Uma retomada aceita somente checkpoints cujo `project_id` e `pipeline_run_id` correspondam à execução atual. O estado também precisa conter todos os artefatos exigidos pelo estágio. No Writer, toda seção marcada como concluída precisa ter payload persistido e validável.

Se três de cinco seções estiverem completas, a retomada reconstrói essas três unidades e chama o modelo apenas para as duas restantes. O artigo montado mantém a ordem do blueprint, independentemente da ordem interna do dicionário persistido.

## Idempotência

Cada checkpoint incremental usa um sufixo baseado na unidade, como `progress:<section_id>`. Eventos de conclusão de agentes incluem o número da tentativa. Isso impede que uma seção posterior ou uma nova tentativa com entrada diferente seja confundida com uma operação anterior.

## Proteções do grafo

A contagem de transições faz parte do checkpoint e continua valendo depois de uma retomada. O grafo bloqueia a execução quando:

- um nó troca `state.stage` diretamente;
- um nó retorna um objeto que não é `V3PipelineState`;
- o número de transições excede `V3_GRAPH_MAX_TRANSITIONS`;
- a retomada contém identidade ou artefatos incompatíveis com o estágio.

Esses casos produzem códigos explícitos e não ficam presos em repetição silenciosa.

## Configuração

```env
V3_INCREMENTAL_WRITER_ENABLED=true
V3_WRITER_SECTION_REPAIR_ATTEMPTS=1
V3_GRAPH_MAX_TRANSITIONS=96
```

Mantenha a geração incremental ativada em produção. Desativá-la preserva o caminho legado de artigo inteiro apenas como mecanismo controlado de compatibilidade.

## Observabilidade esperada

Durante a redação, devem aparecer eventos `v3.writer.unit_started` e `stage.progress`. O progresso informa a seção atual, a quantidade concluída e o total. Cada unidade concluída deve corresponder a um checkpoint próprio.

## Deploy e runs antigos

O manifesto fixa versões de prompts, schemas e feature flags. Uma execução V3 iniciada com contrato anterior deve permanecer imutável. Não retome sob a V3.8 um run ativo cujo manifesto fixo diverge. Aguarde a conclusão antes do deploy ou crie um novo run após atualizar a imagem.

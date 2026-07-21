# Changelog Editorial V3.8

## Geração incremental e retomada segura

### Corrigido

- O Writer deixou de depender de uma única resposta longa para produzir o artigo inteiro. A geração agora ocorre seção por seção, respeitando a ordem do contrato editorial.
- Cada seção concluída é validada e persistida antes da próxima chamada ao modelo. A validação inclui schema, seção correta, H1/title, limites mínimo e máximo de blocos, faixa de palavras e evidência factual.
- Uma unidade inválida recebe reparo dirigido antes de qualquer checkpoint; somente uma unidade válida pode avançar o fluxo.
- Uma retomada reutiliza somente unidades já concluídas e consistentes; seções ausentes continuam do ponto exato em que a execução parou.
- A montagem final do artigo passou a ser determinística, com posições globais contínuas e identificadores estáveis para blocos e sentenças.
- Checkpoints de progresso do Writer recebem chaves de idempotência distintas, eliminando a colisão que poderia descartar estados intermediários.
- Eventos de conclusão de agente agora incluem a tentativa na chave de idempotência, evitando que uma nova tentativa válida seja suprimida.
- O orçamento previsto para uma seção usa uma estimativa de saída própria, em vez de projetar sempre o limite do artigo completo.
- Checkpoints incompatíveis com o projeto, o run ou o estágio atual são bloqueados antes de qualquer continuação.
- O grafo bloqueia mutação direta de estágio, retorno de estado inválido e excesso de transições, evitando loops silenciosos.
- A evidência factual é verificada em cada unidade antes do checkpoint e novamente no artigo montado.

### Novas configurações

```env
V3_INCREMENTAL_WRITER_ENABLED=true
V3_WRITER_SECTION_REPAIR_ATTEMPTS=1
V3_GRAPH_MAX_TRANSITIONS=96
```

`V3_GRAPH_MAX_TRANSITIONS` aceita valores de 24 a 256. O padrão 96 oferece margem para recuperação dirigida sem permitir ciclos ilimitados.

### Contratos e compatibilidade

- Novos contratos pagos fixados no manifesto: `editorial-v3.writer-section.v1` e `editorial-v3.writer-section-repair.v1`.
- Writer completo atualizado para `editorial-v3.writer.v5`.
- Reparo de montagem atualizado para `editorial-v3.writer-repair.v4`.
- Versão lógica do pipeline atualizada para `editorial-v3.8`.
- Nenhuma migration nova. Alembic permanece em `0036`.
- Runs V3 em andamento com manifesto fixo anterior não devem ser retomados sob a nova imagem. Finalize-os antes do deploy ou inicie um novo run após a atualização.

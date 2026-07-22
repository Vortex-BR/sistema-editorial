# Changelog — Editorial V3.6

Data: 20/07/2026

## Editorial Intelligence Core V1

A V3.6 inicia o Motor de Inteligência Editorial sem remover os gates da V3.5.1.
O objetivo desta entrega é criar uma fonte canônica e versionada para conectar o
briefing, o contrato, a pesquisa, as evidências, a redação e as revisões.

### Adicionado

- `ContentIntelligenceState` estrito e versionado por execução.
- Mapa de perguntas editoriais por seção, tipo, criticidade e papel de evidência.
- Plano de seção com objetivo, estado do leitor, dependências, critérios de
  conclusão e claims autorizados/proibidos.
- `EvidenceGraph` com fontes, claims, fatos-fonte, conflitos e vínculos por seção
  e pergunta.
- Política de Writer por claim: `direct`, `conditional`, `context_only` ou
  `prohibited`.
- Gate determinístico de prontidão do Writer.
- Validação determinística do rascunho antes e depois das revisões.
- Persistência de snapshots do estado em `editorial_intelligence_snapshots`.
- Migration `0034_editorial_intelligence_core.py`.
- Estágios `intelligence_planner`, `evidence_graph_builder` e
  `intelligence_gate` no grafo V3.
- Eventos e resumo de inteligência no relatório final de fontes.
- Visualização dos novos estágios no painel do pipeline.

### Alterado

- O Research Planner recebe as perguntas canônicas do novo estado e as incorpora
  aos objetivos e consultas de cada tarefa sem ampliar o número de tarefas nem o
  limite de seis consultas por tarefa.
- O Writer exige lifecycle `writer_ready` e recebe mapa de perguntas, planos de
  seção, catálogo de políticas dos claims e conflitos.
- Development Editor, Fact Checker e Language Editor recebem o mesmo estado
  canônico para evitar interpretações divergentes.
- Frases verificáveis marcadas como editoriais são promovidas
  deterministicamente e exigem claim.
- O suporte claim-frase é recalculado; o `entailment_score` declarado pelo modelo
  não é tratado como autoridade.
- Claims não podem ser usados fora da seção autorizada.
- Claims condicionais precisam carregar condição ou limitação; claims disputados
  precisam de linguagem explícita de incerteza.
- Conflitos não resolvidos e lacunas essenciais bloqueiam a redação.
- A validação é repetida após o Language Editor e no Quality Gate.
- O contrato do pacote final passa a identificar `editorial-v3.6`.
- Readiness e testes de migrations passam a exigir o head `0034`.

### Compatibilidade

- A V2 permanece preservada.
- O schema público do contrato de conhecimento continua `editorial-v3` para não
  invalidar manifests anteriores.
- Grafos de teste que não registram os três novos nós continuam usando o caminho
  legado, mas o executor V3.6 sempre registra os novos estágios.
- Runs antigos não devem ser retomados como V3.6 porque não possuem snapshots e
  checkpoints do novo estado canônico.

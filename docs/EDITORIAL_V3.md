# Editorial Intelligence V3.6.1

## Motor de Inteligência Editorial V3.6.1

A V3.6 introduziu `ContentIntelligenceState`, persistido em snapshots por execução.
A V3.6.1 fecha as relações pergunta→claim→frase→fonte, adiciona recuperação orientada pelo gate e vincula a aprovação ao hash exato do artefato. Ele liga o contrato às perguntas editoriais, aos planos de seção, ao grafo de
evidências e às políticas de uso de claims. O Research Planner incorpora perguntas
canônicas; o Writer recebe somente claims autorizados por seção; e o estado é
validado no intelligence gate, no Writer, após o Language Editor e no Quality Gate.
A migration head é `0036`.


## Visão geral

A V3.6.1 é um pipeline de pesquisa e redação orientado por contrato e estado canônico. Antes de buscar fontes, o sistema escolhe uma arquitetura editorial, constrói um grafo ordenado de conhecimento e cria uma intenção factual canônica. O texto só é gerado depois que os nós factuais obrigatórios possuem evidência relevante, legível, independente e adequada ao papel exigido.

Fluxo principal:

```text
content_contract
→ knowledge_architect
→ knowledge_gate
→ intelligence_planner
→ research_planner
→ source_discovery
→ source_reader
→ source_coverage_gate
→ targeted_source_recovery (quando necessário)
→ knowledge_synthesizer
→ evidence_graph_builder
→ intelligence_gate
→ targeted_source_recovery (quando houver blocker recuperável)
→ rebuild do Evidence Graph e novo intelligence_gate
→ knowledge_completeness_gate
→ writer
→ development_editor
→ fact_checker
→ language_editor
→ external_reference_gate
→ finalizer
→ quality_gate
→ human_approval
→ export
```

## Arquiteturas suportadas

- guia explicativo;
- guia procedural com decisão;
- comparação;
- troubleshooting;
- educação comercial.

Somente o guia procedural exige abordagens nomeadas, comparação e referência externa por abordagem. Os demais tipos usam dossiês por nó editorial.

Consulte `docs/UNIVERSAL_EDITORIAL_HIERARCHY.md` para os contratos e gates compartilhados com o V2.

## Pipeline procedural

O contrato procedural preserva o grafo detalhado de 13 nós:

- fundamento do objeto;
- inventário de abordagens;
- requisitos comuns;
- comparação;
- seleção;
- execução;
- sinais de progresso;
- decisão de transição;
- execução da transição;
- monitoramento posterior;
- resultado final;
- troubleshooting;
- referências externas.

Esses nós são mapeados para funções universais e recebem peso de profundidade. A execução só avança quando cada abordagem obrigatória possui cobertura compatível com o contrato.

## Pesquisa V3.5 orientada por intenção

A V3.5 separa palavra-chave SEO de assunto factual, seleciona mercados conforme locale, jurisdição e papel da evidência e localiza a consulta para cada mercado. A busca compartilha orçamento real e circuit breaker entre tarefas. Depois da leitura, um gate mede cobertura por nó; lacunas recuperáveis geram consultas dirigidas antes do bloqueio final.

Consulte `docs/EDITORIAL_V3_5_RESEARCH.md` para a arquitetura operacional, limites e diagnóstico.

## Pesquisa equilibrada e suplementar

A descoberta inicial usa `node_round_robin.v1`: todos os nós recebem uma consulta antes de qualquer nó receber a segunda. O run bloqueia quando o orçamento total não comporta ao menos uma consulta por nó.

Uma parte do orçamento fica reservada para lacunas. Depois da extração e aprovação inicial dos claims, o sistema mede a cobertura por nó, consome consultas planejadas ainda não executadas e, quando necessário, gera consultas dirigidas ao gap. Fontes já conhecidas podem ser reassociadas a novos nós sem consumir uma vaga de documento.

## Taxonomia das abordagens

O briefing procedural declara `required_approach_type`, como `method`, `environment`, `system`, `strategy` ou `technique`. Antes da pesquisa, o Knowledge Architect verifica se os itens são pertinentes, pertencem à dimensão declarada e estão no mesmo nível de abstração. Misturas entre ambiente, técnica, material e etapa bloqueiam o run.

## Pipelines não procedurais

Nos tipos explicativo, comparação, troubleshooting e educação comercial:

- o grafo é derivado da Arquitetura Editorial Universal;
- o planner pesquisa por nó;
- a síntese produz dossiês de seção;
- a matriz de abordagens é omitida;
- o Writer é proibido de inventar abordagens ou passos;
- a qualidade é avaliada pela rubrica `quality-rubric.universal-editorial.v1`.

## Aprovação e exportação

A publicação continua exigindo revisão humana. O pacote final mantém checksum, rastreabilidade factual, relatório de fontes e metadados da arquitetura editorial utilizada.

## Ativação

A V3 requer:

```env
EDITORIAL_PIPELINE_V3_ENABLED=true
EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED=true
```

E banco migrado para o head `0036`.

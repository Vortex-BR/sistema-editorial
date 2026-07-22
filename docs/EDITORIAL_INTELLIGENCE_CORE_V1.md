# Motor de Inteligência Editorial — Core V1

> Documento-base da V3.6. A integridade de fluxo atual está em `EDITORIAL_INTELLIGENCE_V3_6_1.md`; o head operacional é `0036`.
## Por que o núcleo existe

Uma sequência de agentes não garante inteligência editorial. Sem um estado
comum, o pesquisador, o sintetizador, o Writer e os revisores podem trabalhar com
interpretações diferentes do objetivo, das evidências e da responsabilidade de
cada seção.

O Core V1 introduz um objeto canônico que acompanha toda a execução:

```text
ContentIntelligenceState
├── objetivo, intenção, leitor, marca e restrições
├── perguntas editoriais
├── planos de seção e dependências
├── grafo de evidências
│   ├── fontes
│   ├── claims
│   ├── fatos-fonte
│   └── conflitos
├── lacunas essenciais
├── validação
├── lifecycle e revisão
└── checksum
```

## Lifecycle

```text
planned
→ evidence_attached
→ writer_ready
→ draft_validated
```

Qualquer gate pode mover o estado para `blocked`. Um estado bloqueado não pode
ser usado pelo Writer.

## Fluxo V3.6

```text
briefing + contrato
→ intelligence_planner
→ mapa de perguntas e planos de seção
→ research_planner orientado pelas perguntas
→ descoberta, leitura e síntese
→ evidence_graph_builder
→ intelligence_gate
→ knowledge_completeness_gate
→ Writer restrito
→ Development Editor
→ Fact Checker
→ Language Editor
→ fact-check pós-linguagem
→ validação de inteligência final
→ Quality Gate
→ revisão humana
```

## Mapa de perguntas

Cada nó ativo produz perguntas de quatro tipos:

- `central`: função principal da seção;
- `knowledge`: conceitos e fatos que precisam ser explicados;
- `decision`: critérios que o leitor deve conseguir aplicar;
- `completion`: sinal de que a seção cumpriu seu papel.

As perguntas críticas e que exigem pesquisa precisam de claims utilizáveis antes
da redação. O plano de pesquisa recebe essas perguntas para reduzir a distância
entre “fonte encontrada” e “necessidade editorial resolvida”.

## Plano de seção

Cada seção registra:

- função e objetivo;
- estado do leitor antes e depois;
- dependências;
- perguntas sob sua responsabilidade;
- necessidade de pesquisa;
- profundidade mínima;
- claims autorizados e proibidos;
- conflitos e condições obrigatórias;
- conclusões proibidas;
- critérios de conclusão.

O Writer não pode mover livremente um claim para outra seção apenas porque o
texto parece conveniente.

## Grafo de evidências

O grafo liga claims aprovados aos documentos e fatos-fonte persistidos. A
política de uso é derivada de `conclusion_status`:

| Status | Política do Writer |
|---|---|
| `confirmed` | `direct` |
| `well_supported` | `direct` |
| `conditional` | `conditional` |
| `disputed` | `context_only` |
| `insufficient_evidence` | `prohibited` |

Um ID de fonte isolado não basta. O claim precisa manter seção, papel de
evidência, fato-fonte, condição, limitação, aplicabilidade, confiança e conflito.

## Gates determinísticos

### Antes do Writer

O sistema verifica fechamento de referências, claims por seção, fatos-fonte,
perguntas críticas, conflitos e lacunas.

### Durante e depois da redação

O sistema verifica factualidade determinística, presença de claim, autorização
por seção, suporte claim-frase, números, negação, condições, linguagem de
incerteza, conclusões proibidas e cobertura das seções.

A validação não depende apenas do JSON de aprovação do próprio modelo.

## Persistência

A tabela `editorial_intelligence_snapshots` guarda snapshots por run, revisão e
estágio. O estado pode ser auditado sem reconstrução a partir de logs soltos.

A migration `0034` criou o snapshot-base; o head operacional atual é `0036`.

## O que ainda precisa evoluir

O Core V1 é a fundação, não a versão final de compreensão semântica. Permanecem
para uma próxima etapa:

1. classificador semântico/NLI independente e calibrado por idioma;
2. recuperação de trechos exatos e ranking por pergunta, não só por documento;
3. resolução formal de conflitos com decisão humana quando necessário;
4. grafo tópico entre artigos para planejamento, cobertura e canibalização;
5. interface visual para inspecionar perguntas, claims, fontes e overrides;
6. aprendizado controlado a partir de edições humanas aprovadas;
7. benchmark de qualidade, custo e latência em diferentes nichos;
8. detecção mais profunda de omissões, redundância e ganho informacional.

Esses itens devem ser adicionados sobre o estado canônico, e não como novos
agentes soltos.

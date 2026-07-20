# Relatório de implementação — Editorial V3.5.1 Generation Hardening

Data: 20/07/2026

## 1. Escopo entregue

A implementação corrige o conjunto de riscos `GEN-001` a `GEN-024` documentado em `docs/ANALISE_ROBUSTA_GERACAO_CONTEUDO_V3_5.md`. O trabalho foi aplicado sobre a V3.5 e mantém intactos os componentes de pesquisa orientada por intenção, orçamento, circuit breaker e recuperação dirigida.

O objetivo desta entrega é tornar confiável o pipeline atual antes da construção do Motor de Inteligência Editorial. Não foram introduzidos novos agentes apenas para mascarar o problema; foram fortalecidos os contratos, o estado, a validação determinística e a fronteira entre dados, instruções e publicação.

## 2. Arquitetura corrigida

```text
briefing + perfil + contrato
→ GenerationBrief canônico
→ resolução de nós aplicáveis
→ pesquisa V3.5
→ catálogo de claims autorizado por seção
→ task_data público e delimitado
→ writer com orçamento e estrutura tipada
→ diagnóstico determinístico do rascunho
→ reparo estrutural limitado
→ editor de desenvolvimento
→ fact-checker independente
→ editor de linguagem com preservação
→ novo fact-check pós-edição
→ conformidade do briefing e SEO
→ similaridade/canibalização
→ gate universal/procedural
→ persistência final
→ pacote de revisão humana
```

## 3. Mapeamento da auditoria para a implementação

| ID | Correção aplicada | Resultado esperado |
|---|---|---|
| GEN-001 | `task_data` público separado de `trace_input`, envelope não confiável, limite, secret scan e hash de entrada | Modelos recebem contrato, claims, documentos e rascunho sem expor metadados privados |
| GEN-002 | Classificação factual conservadora, validação de cobertura, entailment determinístico e consistência do fact-check | `passed` não pode ser autodeclarado sem checks completos |
| GEN-003 | Quote matching ordenado/local, transliteração estável e compatibilidade de claims | Palavras embaralhadas e claims incompatíveis deixam de formar evidência |
| GEN-004 | `claim_catalog` por seção entregue ao writer/revisores | Frase factual pode apontar para o claim correto, com condições e limites |
| GEN-005 | `generation_brief` integra objetivo, audiência, marca, oferta, CTA, SEO e restrições | Conteúdo deixa de depender de instruções genéricas |
| GEN-006 | Locale do projeto governa prompts, skills e validação de idioma | Projetos `pt-BR`, `en-US` e `es-ES` são tratados explicitamente |
| GEN-007 | Nó de fechamento preservado e CTA condicionado ao briefing | Artigo não termina abruptamente nem inventa oferta |
| GEN-008 | Resolução explícita de aplicabilidade de cada nó | Seções opcionais não viram filler obrigatório |
| GEN-009 | Preservação de números/negação/URLs/shape e fact-check pós-linguagem | Revisões não alteram silenciosamente fatos aprovados |
| GEN-010 | Validators de review rejeitam status inconsistente e cobertura incompleta | Review vazio ou contraditório não passa |
| GEN-011 | Rotas e skills próprias para três revisores | Reduz correlação de função e permite políticas/modelos distintos |
| GEN-012 | Tabelas/callouts tipados, payload JSONB, citações numéricas e fontes visíveis | Estrutura e rastreabilidade sobrevivem à persistência/finalização |
| GEN-013 | Faixa efetiva respeita mínimo e máximo do briefing | Artigo não é inflado pelo default global |
| GEN-014 | Gates para H2/H3, seções, claims proibidos e links | Entrega não ignora campos aceitos pela API |
| GEN-015 | Slug Unicode, SEO determinístico e validação de metadados | Evita slugs quebrados e metadados incompatíveis com o corpo |
| GEN-016 | Headings e metadados entram na análise factual | Claim forte não escapa por estar fora dos parágrafos |
| GEN-017 | `procedural_how_to` de caminho único | Guia simples não é forçado a comparar métodos inexistentes |
| GEN-018 | Remoção de HTML oculto e filtro de instruções em fontes | Fonte pesquisada não ganha autoridade de prompt |
| GEN-019 | Seleção de fragmentos por relevância e limites explícitos | Evidência útil no fim do documento pode chegar ao agente |
| GEN-020 | Promoção de campos finais somente após aprovação | Conteúdo bloqueado não substitui entrega aprovada |
| GEN-021 | Preflight de output budget para writer/fact-checker e rotas ampliadas | Menos risco de JSON incompleto ou artigo truncado |
| GEN-022 | Similaridade textual e colisão de intenção/keyword no mesmo escopo | Duplicação e canibalização são detectadas antes da publicação |
| GEN-023 | Hash de `task_data` na idempotência | Mudança de estado força nova execução real |
| GEN-024 | Skills revisadas, locale explícito e correções gramaticais | Prompts menos contraditórios e menos artificiais |

## 4. Componentes principais

### 4.1 Agent runtime

`backend/app/services/agent_runtime.py` agora:

- recebe `task_data` explicitamente;
- rejeita conteúdo sensível;
- delimita o payload público;
- mede tamanho e hash;
- inclui a entrada no cálculo de idempotência;
- faz preflight do orçamento de writer e fact-checker;
- continua armazenando somente metadados seguros na trilha automática.

### 4.2 Contexto de geração

`generation_context.py` produz um objeto canônico com:

- locale, objetivo e tipo editorial;
- keyword principal e secundárias;
- audiência e estado de transformação;
- marca, voz, oferta e CTA;
- estrutura obrigatória;
- política de evidência;
- links e contexto adicional;
- resolução de nós requeridos, condicionais e opcionais.

### 4.3 Integridade textual e factual

`text_integrity.py` fornece uma base determinística para:

- slug e IDs estáveis;
- quote matching com ordem;
- números e unidades;
- negação;
- marcadores factuais;
- compatibilidade entre frase e claim;
- preservação de revisões.

O modelo continua útil para julgamento semântico, mas não pode mais neutralizar essa camada declarando uma frase como não factual ou um review como aprovado.

### 4.4 Qualidade de idioma e briefing

`language_quality.py` identifica incompatibilidade material entre idioma esperado e texto. O executor também verifica faixa de palavras, headings, seções obrigatórias, keyword, claims proibidos, link interno, cobertura de métodos e estrutura.

### 4.5 Similaridade

`content_similarity.py` calcula:

- shingles do corpo sem boilerplate de fontes;
- sobreposição de intenção;
- colisão exata de keyword;
- similaridade de título;
- fingerprint do candidato.

A consulta é limitada ao mesmo escopo editorial para evitar comparação entre clientes ou marcas sem relação.

### 4.6 Estruturas tipadas

`V3TableRow`, tabelas retangulares e callouts com título/corpo são validados no schema. `structured_payload` é persistido em cada `ArticleBlock`, enquanto `text` permanece como representação compatível para consumidores antigos.

### 4.7 Revisores

Foram adicionadas funções e skills específicas:

- `development_editor`;
- `fact_checker`;
- `language_editor`.

Cada uma pode usar rota e orçamento próprios. O executor rejeita alterações que mudem formato, números, negação, URLs ou significado sem revalidação.

## 5. Banco de dados

A migration `0033` é obrigatória. Ela adiciona o JSONB não nulo `article_blocks.structured_payload` com default `{}`. Não há backfill destrutivo; blocos antigos continuam válidos com objeto vazio.

## 6. Compatibilidade operacional

- V2 permanece selecionável.
- V3.5.1 exige as duas flags V3 já existentes.
- Readiness exige Alembic head `0033`.
- Nova execução é obrigatória depois do deploy; checkpoint V3.5 não contém todos os artefatos e hashes da V3.5.1.
- Nenhum segredo foi colocado em prompt, documentação ou pacote.

## 7. O que fica para o Motor de Inteligência Editorial

Esta entrega não implementa ainda:

- estado editorial canônico versionado entre todos os agentes;
- grafo completo de entidades, perguntas, claims, fontes, conflitos e seções;
- resolução semântica profunda de contradições entre fontes;
- planejamento adaptativo baseado em ganho informacional;
- aprendizado editorial com artigos aprovados e feedback humano.

Esses componentes devem formar a próxima fase, agora sobre um pipeline que transporta e valida corretamente seus dados.

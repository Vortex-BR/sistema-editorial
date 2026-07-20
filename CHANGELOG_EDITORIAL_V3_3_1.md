# Changelog — Editorial V3.3.1 Research Coverage Hardening

## Pesquisa por hierarquia

- substituída a execução sequencial de consultas por `node_round_robin.v1`;
- cada nó de pesquisa recebe uma consulta antes de qualquer nó consumir a segunda;
- nós críticos continuam priorizados quando o orçamento é insuficiente;
- a execução é bloqueada com `V3_QUERY_BUDGET_INSUFFICIENT` quando o limite total não comporta ao menos uma consulta por nó;
- métricas registram consultas executadas por tarefa, nós cobertos e nós ainda sem cobertura.

## Pesquisa suplementar

- restaurada e integrada a execução real da reserva de consultas;
- consultas planejadas ainda não utilizadas são consumidas antes de consultas geradas para lacunas;
- a reserva também usa round-robin entre os nós incompletos;
- fontes já descobertas podem ser reassociadas a novos nós mesmo quando o limite de novos documentos foi atingido;
- métricas registram modo da consulta, nós direcionados, fontes aceitas e lacunas restantes.

## Taxonomia de abordagens

- adicionado `required_approach_type` ao briefing;
- dimensões suportadas: método, ambiente, sistema, estratégia, técnica, material, canal, formato, opção e outra;
- o Knowledge Architect valida, antes da pesquisa, se todas as abordagens pertencem à dimensão declarada e ao mesmo nível de abstração;
- misturas como ambiente + técnica + etapa bloqueiam o run com `V3_APPROACH_TAXONOMY_INVALID`;
- textos editoriais usam “abordagem” como termo universal, preservando os nomes internos antigos apenas para compatibilidade.

## Escopo e faixa de palavras

- o mínimo estrutural procedural passou a ter uma função única compartilhada pela API e pelo executor;
- projetos são rejeitados antes da execução quando `maximum_words` não comporta o número de abordagens e nós;
- runs históricos incompatíveis bloqueiam em `content_contract` com `V3_WORD_RANGE_SCOPE_CONFLICT`.

## Correções de runtime

- restaurados no executor V3 os métodos de extração de claims, pesquisa suplementar e revisão localizada;
- adicionado o contrato `ApproachTaxonomyValidationOutput` ao manifesto V3;
- o pacote deixa de depender de testes indiretos para detectar métodos auxiliares ausentes.

## Validação

- 840 testes backend aprovados;
- 40 testes backend ignorados por condições explícitas;
- 63 testes frontend aprovados;
- Ruff, compileall, ESLint, TypeScript e build Vite aprovados;
- npm audit sem vulnerabilidades reportadas;
- dry-run “guia de cultivo”: 13/13 nós com pesquisa inicial, 28 consultas iniciais e reserva de 8 consultas suplementares.

# Relatório de implementação — V3.3.1 Research Coverage Hardening

**Data:** 18 de julho de 2026  
**Base:** V3.3 Universal Editorial Hierarchy  
**Objetivo:** corrigir os bloqueadores encontrados no dry-run de “guia de cultivo” antes da ativação em produção.

## 1. Problemas corrigidos

### Distribuição desigual das consultas

O executor percorria as tarefas em sequência. Os primeiros nós consumiam o limite inicial e os últimos podiam ficar sem nenhuma consulta.

A execução agora utiliza `schedule_research_queries()` com estratégia round-robin. Com 13 tarefas e limite inicial de 28 consultas, todos os 13 nós recebem duas consultas e os dois nós com consultas adicionais recebem a terceira somente depois da segunda rodada completa.

### Reserva suplementar inoperante

O executor chamava métodos auxiliares que estavam ausentes no pacote V3.3. Foram restaurados e integrados:

- `_extract_claims_for_tasks()`;
- `_supplement_research()`;
- `_review_and_revise()`.

A pesquisa suplementar agora:

1. mede cobertura aprovada por nó;
2. seleciona nós abaixo do mínimo de fontes independentes;
3. consome consultas planejadas ainda não executadas;
4. gera consultas direcionadas somente quando necessário;
5. pesquisa em round-robin;
6. lê e persiste novas fontes;
7. reassocia fontes existentes quando aplicáveis;
8. extrai e reapresenta claims para aprovação;
9. registra a cobertura antes e depois.

### Abordagens semanticamente incompatíveis

O briefing passou a declarar a dimensão das alternativas. O Knowledge Architect usa um contrato estruturado para verificar:

- pertinência ao tópico;
- correspondência com a dimensão declarada;
- comparabilidade no mesmo nível de abstração;
- ausência de mistura entre alternativa, etapa, técnica, material e ambiente.

O run é bloqueado antes da pesquisa quando a taxonomia não é coerente.

### Faixa de palavras incompatível

O sistema calcula o mínimo estrutural antes de consumir pesquisa. Para três abordagens e treze nós, o mínimo é 2.395 palavras. Um máximo inferior é rejeitado pela API e, para runs históricos, pelo stage `content_contract`.

## 2. Alterações principais

- `backend/app/services/editorial_v3/research_planner.py`
  - scheduler round-robin puro e testável;
- `backend/app/orchestration/v3/executor.py`
  - distribuição inicial;
  - pesquisa suplementar;
  - validação taxonômica;
  - bloqueios de orçamento e escopo;
  - restauração dos helpers de runtime;
- `backend/app/schemas/editorial_v3.py`
  - `ApproachDimension`;
  - mínimo estrutural compartilhado;
  - dimensão persistida no contrato;
- `backend/app/schemas/editorial_v3_runtime.py`
  - contratos de validação taxonômica;
- `backend/app/schemas/api.py`
  - `required_approach_type`;
  - validação antecipada de faixa de palavras;
- `backend/app/services/execution_manifest.py`
  - versão e checksum do novo contrato;
- `frontend/src/pages/NewProject.tsx`
  - seleção explícita da dimensão;
  - terminologia “abordagens”;
  - estimativa do mínimo estrutural;
- `backend/tests/test_editorial_v3_research_hardening.py`
  - testes de alocação, reserva, taxonomia e escopo.

## 3. Resultado do dry-run

Tema: `guia de cultivo de cannabis`  
Dimensão: `environment`  
Abordagens: ambiente interno, externo e protegido.

- 11 nós universais;
- 13 nós V3;
- 13 tarefas de pesquisa;
- 36 consultas máximas;
- 28 consultas iniciais;
- 8 consultas reservadas;
- 13/13 tarefas cobertas inicialmente;
- nenhum nó obrigatório sem consulta;
- gate do plano aprovado;
- gate estrutural do rascunho aprovado.

## 4. Limitações da validação

Não foram realizadas chamadas pagas reais a LLM, Serper ou Tavily, nem deploy conectado à infraestrutura de produção. A validação cobre contratos, agendamento, bloqueios, suíte automatizada, frontend e dry-run determinístico.

## 5. Recomendação de implantação

1. aplicar o pacote em staging;
2. manter as flags V3 de execução desativadas durante o deploy;
3. validar readiness e Alembic head `0032`;
4. criar um novo projeto procedural com dimensão explícita;
5. observar os eventos `v3.sources.discovered`, `v3.research.supplemented` e `v3.approach_taxonomy.validated`;
6. confirmar `initial_uncovered_task_ids=[]`;
7. ativar canário com revisão humana integral;
8. somente depois habilitar produção gradual.

# Relatório de implementação — V3.3 Universal Editorial Hierarchy

**Data:** 18 de julho de 2026  
**Base:** Editorial V3.2 Human Prose  
**Resultado:** refatoração estrutural concluída e validada localmente

## 1. Objetivo atendido

A atualização substitui a dependência de instruções genéricas de “coerência” por uma arquitetura editorial tipada e verificável. Antes da pesquisa, o sistema determina a transformação do leitor, os nós necessários, a ordem, as dependências, a centralidade, a necessidade de evidência e os critérios de conclusão.

A implementação não contém skill universal baseada em germinação. A estrutura é independente do nicho e foi testada em automotivo, telecomunicações, suporte técnico e serviços B2B.

## 2. Componentes implementados

### Contratos universais

- `backend/app/schemas/editorial_hierarchy.py`
- `backend/app/services/editorial_hierarchy.py`

Foram criados cinco tipos editoriais:

- `explanatory_guide`;
- `procedural_decision_guide`;
- `comparison`;
- `troubleshooting`;
- `commercial_education`.

Cada nó possui:

- identidade estável;
- sequência;
- função universal;
- estado do leitor antes e depois;
- pergunta central;
- dependências;
- aplicabilidade;
- importância;
- exigência de pesquisa;
- critérios de conclusão;
- peso mínimo e máximo de profundidade.

### Gates determinísticos

Foram adicionadas validações para:

- cobertura dos nós factuais na pesquisa;
- cobertura dos nós no blueprint;
- referências a nós inexistentes;
- ordem lógica;
- dependências;
- cobertura do rascunho;
- blocos sem identidade hierárquica;
- fechamento prematuro;
- superficialidade de nós centrais;
- inversão entre conteúdo central e periférico.

## 3. Pipeline V2

O V2 agora:

1. constrói a hierarquia antes do Planner;
2. injeta o contrato no planejamento;
3. exige `node_ids` nas perguntas;
4. exige `node_ids` nas seções do blueprint;
5. persiste a hierarquia e os vínculos;
6. mede evidência por nó;
7. bloqueia pesquisa incompleta antes do Writer;
8. exige identidade de nó nos blocos do artigo;
9. executa reparo estrutural dirigido;
10. bloqueia o run se a hierarquia continuar inválida;
11. revalida a estrutura durante a edição.

## 4. Pipeline V3

### Conteúdo procedural

O grafo detalhado de 13 nós foi preservado. Cada nó recebeu uma função universal, peso e centralidade. Métodos, comparação, passos e referências por método continuam obrigatórios apenas nesse tipo.

### Conteúdo não procedural

Explicação, comparação, troubleshooting e educação comercial passaram a usar contratos próprios. O pipeline deixou de exigir artificialmente:

- inventário de métodos;
- dossiê por método;
- passo a passo;
- matriz procedural;
- links por método.

Foram atualizados:

- knowledge contract;
- research planner;
- knowledge synthesizer;
- artifact repository;
- knowledge completeness;
- Writer;
- Development Editor;
- finalizer;
- quality gate.

A rubrica não procedural é `quality-rubric.universal-editorial.v1`.

## 5. Produto e briefing

A interface agora permite selecionar a arquitetura em V2 e V3. O padrão é `explanatory_guide`.

Os controles de métodos aparecem somente quando:

```text
pipeline = V3
architecture = procedural_decision_guide
```

Os exemplos e placeholders foram substituídos por temas independentes do nicho original.

## 6. Banco e CI

Nova migration:

```text
0032_universal_editorial_hierarchy
```

Ela adiciona:

- `research_plans.hierarchy_json`;
- `research_questions.node_ids`;
- prioridade máxima 20 para perguntas.

Foram atualizados:

- workflow de build Docker;
- readiness;
- testes de migration;
- documentação;
- head esperado do Alembic.

Validação:

```text
alembic heads
0032 (head)
```

## 7. Compatibilidade

Os nomes antigos `germination_confirmation` permanecem apenas como aliases de leitura para artefatos históricos. O nome canônico atual é `outcome_confirmation`.

Nenhum prompt, skill, template, regra de qualidade ou tela usa os termos antigos.

Runs existentes preservam seus manifestos. A arquitetura nova entra em vigor em novos runs.

## 8. Testes executados

### Backend

```text
832 passed
40 skipped
1 warning de depreciação externa do Starlette TestClient
```

Também aprovados:

- `compileall`;
- Ruff;
- 21 testes novos da arquitetura universal;
- Alembic com head único `0032`;
- carregamento de 18 skills default e 8 skills V3.

### Frontend

```text
63 passed
8 arquivos de teste
```

Também aprovados:

- ESLint;
- TypeScript;
- build Vite;
- npm audit sem vulnerabilidades reportadas.

### Casos cross-domain

- troca de óleo do carro;
- portabilidade numérica;
- planos de internet residencial;
- bateria de notebook sem carga;
- backup gerenciado para pequenas empresas.

## 9. Arquivos principais alterados

### Novos

- `backend/app/schemas/editorial_hierarchy.py`
- `backend/app/services/editorial_hierarchy.py`
- `backend/app/services/editorial_v3/universal_quality.py`
- `backend/alembic/versions/0032_universal_editorial_hierarchy.py`
- `backend/tests/test_editorial_hierarchy.py`
- `docs/UNIVERSAL_EDITORIAL_HIERARCHY.md`
- `docs/EDITORIAL_V3.md`
- `docs/EDITORIAL_V3_PRODUCTION_RUNBOOK.md`
- `CHANGELOG_EDITORIAL_V3_3.md`

### Refatorados

- `backend/app/orchestration/executor.py`
- `backend/app/orchestration/v3/executor.py`
- `backend/app/schemas/agents.py`
- `backend/app/schemas/api.py`
- `backend/app/schemas/editorial_v3.py`
- `backend/app/schemas/editorial_v3_runtime.py`
- `backend/app/services/research_coverage.py`
- `backend/app/services/editorial_v3/knowledge_contract.py`
- `backend/app/services/editorial_v3/research_planner.py`
- `backend/app/services/editorial_v3/knowledge_completeness.py`
- `backend/app/services/editorial_v3/artifact_repository.py`
- `frontend/src/pages/NewProject.tsx`
- skills de estilo, SEO e pesquisa para remover exemplos de nicho.

## 10. Limitações da validação

A validação foi feita com testes determinísticos, mocks de providers, lint, build e schemas. Não foram executados nesta sessão:

- chamadas pagas reais a LLMs;
- pesquisa real em Serper/Tavily;
- deploy completo no EasyPanel;
- migration contra uma cópia do banco de produção;
- canário com revisão humana de artigos reais.

Antes da ativação ampla, recomenda-se aplicar `0032` em staging e executar um run novo de cada arquitetura editorial.

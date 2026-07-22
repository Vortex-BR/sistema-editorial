# Validação — Editorial V3.3.1

**Data:** 18 de julho de 2026

## Backend

```text
compileall: aprovado
Ruff: aprovado
pytest: 840 passed, 40 skipped
Alembic head: 0032
```

O único aviso é uma depreciação externa do `Starlette TestClient` relacionada ao pacote `httpx`.

## Frontend

```text
Vitest: 63 passed em 8 arquivos
ESLint: aprovado
TypeScript/Vite: aprovado
npm audit: 0 vulnerabilidades reportadas
```

## Testes novos de hardening

- round-robin cobre todos os nós antes de aprofundar;
- consultas já executadas são ignoradas na passagem suplementar;
- source discovery cobre 13/13 tarefas com o orçamento padrão;
- reserva suplementar é efetivamente consumida;
- taxonomia mista bloqueia antes da pesquisa;
- faixa máxima abaixo do mínimo estrutural é rejeitada;
- dimensão `environment` é persistida corretamente;
- orçamento menor que a quantidade de nós bloqueia o run.

## Dry-run “guia de cultivo”

```text
arquitetura universal: procedural_decision_guide
nós universais: 11
nós V3: 13
tarefas de pesquisa: 13
consultas máximas: 36
consultas iniciais: 28
reserva suplementar: 8
nós cobertos inicialmente: 13/13
nós sem consulta inicial: 0
gate do plano: aprovado
gate estrutural do rascunho: aprovado
dimensão das abordagens: environment
```

## Não validado neste ambiente

- chamadas pagas reais aos modelos;
- pesquisa real em Serper/Tavily;
- deploy no EasyPanel;
- migração sobre cópia do banco de produção;
- throughput e custos sob carga real.

# Validação — Editorial Intelligence V3.2

Data: 18/07/2026

## Resultado

Aprovado nos testes disponíveis neste ambiente.

## Backend

- Ruff: aprovado.
- Python `compileall`: aprovado.
- Pytest: 810 aprovados, 40 ignorados.
- Aviso conhecido: depreciação do `TestClient` de teste.
- `pip check`: nenhuma dependência quebrada.
- Alembic: head único `0031`.
- SQL offline upgrade `0030 -> 0031`: aprovado.
- SQL offline downgrade `0031 -> 0030`: aprovado.

## Frontend

- Vitest: 63 testes aprovados em 8 arquivos.
- ESLint: aprovado.
- TypeScript e Vite build: aprovados.

## Testes novos

- Conteúdo condensado semelhante ao primeiro rascunho é identificado por abertura
  numérica, falta de orientação por métodos, excesso de subtítulos e parágrafos-resumo.
- Uma amostra editorial desenvolvida, com métodos apresentados cedo, progressão e
  variação funcional de ritmo, não recebe os mesmos bloqueios.
- O contrato passa a exigir `method_inventory` antes de `process_requirements`.

## Limitações

Não foram executadas chamadas reais à OpenAI, Serper ou Tavily, nem um deploy com
PostgreSQL, Redis, Celery e Docker neste ambiente. A validação final exige um artigo
canário com o mesmo briefing usado anteriormente, seguido de revisão humana e
comparação de custo, profundidade, naturalidade e bloqueadores.

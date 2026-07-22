# Validação da atualização Editorial V3.1

Data: 17/07/2026

## Base utilizada

- Pacote: `docker-seo-editorial-v3-complete-audited-hotfix-ci-2026-07-17.zip`
- SHA-256 da base: `6edfa547a72ea0f3b5549062e640604c4065c27d2625bd9e5977990beda4a99f`

## Problemas tratados

- pesquisa aprovada sem cobrir os métodos prometidos;
- poucos claims por método;
- Writer iniciado apesar de lacunas essenciais;
- artigo curto, sem passos completos e sem seção própria para cada método;
- regeneração genérica sem diagnóstico estrutural;
- score alto mesmo com blocker crítico;
- troca do nome do modelo preservando preços e limites do modelo anterior;
- teto de saída insuficiente ou incompatível com o orçamento escolhido.

## Controles implementados

1. `required_methods` faz parte do briefing e do contrato V3.
2. Guias procedurais exigem pelo menos dois métodos nomeados.
3. A pesquisa cria consultas específicas para os métodos obrigatórios.
4. A correspondência de método considera nome, aliases, acentos e variações equivalentes.
5. Cada método exige, por padrão, três claims aprovados, três passos e uma referência externa independente.
6. O Writer não começa enquanto o Knowledge Completeness Gate detectar ausência essencial.
7. Blocos específicos carregam `method_id`; métodos sem heading, passos ou profundidade própria são bloqueados.
8. A faixa de tamanho cresce conforme métodos e seções do contrato.
9. Há no máximo uma reparação estrutural, alimentada pelos blockers exatos.
10. Persistindo falhas após a reparação, o fluxo para antes dos editores, evitando mais gasto.
11. Qualquer blocker crítico limita o score exibido a 59%.
12. Modelos OpenAI conhecidos recebem preços e limites canônicos no servidor ao salvar a rota.
13. A migration `0030` atualiza rotas já salvas que correspondam exatamente ao roteamento recomendado.
14. O Writer usa teto de 12.000 tokens, preservando espaço para JSON e compatibilidade com `MAX_AGENT_COST_USD=0.40`.

## Roteamento validado

- Planner: `gpt-5-mini`
- Researcher: `gpt-5-mini`
- Research Gatekeeper: `gpt-5.4-mini`
- Writer: `gpt-5.4`
- Editor: `gpt-5.4-mini`
- Skill Curator: `gpt-5-mini`

## Orçamento recomendado para o canário

```env
MAX_PIPELINE_COST_USD=0.80
MAX_AGENT_COST_USD=0.40
V3_MIN_CLAIMS_PER_METHOD=3
V3_MIN_STEPS_PER_METHOD=3
V3_WRITER_REPAIR_ATTEMPTS=1
```

## Validações executadas

### Backend

- Ruff: aprovado.
- `compileall`: aprovado.
- Pytest: **808 aprovados, 40 ignorados, 1 aviso de depreciação do TestClient**.
- `pip check`: nenhuma dependência quebrada.
- Alembic head único: **0030**.
- SQL offline upgrade `0029 -> 0030`: aprovado.
- SQL offline downgrade `0030 -> 0029`: aprovado.

### Frontend

- ESLint: aprovado.
- Vitest: **63 testes aprovados em 8 arquivos**.
- TypeScript + Vite build: aprovado.

### Conteúdo e empacotamento

- Benchmark de germinação atualizado para exigir papel-toalha, copo com água, jiffy e plantio direto no substrato.
- JSON: 6 arquivos validados.
- YAML: 35 arquivos validados.
- O pacote final exclui `.env`, `.git`, `node_modules`, `dist`, caches, bytecode e cobertura.

## Limitações honestas

- Docker não está instalado no ambiente de validação; o build da imagem deve ser confirmado pelo EasyPanel/GitHub após o push.
- PostgreSQL, Redis e Celery reais não foram executados nesta validação local.
- Nenhuma chamada paga a OpenAI ou ao mecanismo de busca foi realizada.
- A qualidade editorial final ainda depende de um novo artigo canário e revisão humana.

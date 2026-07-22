# Editorial V3.1 — cobertura procedural, reparo e orçamento confiável

Data: 17/07/2026

## Objetivo

Corrigir o padrão observado no primeiro artigo canário: pesquisa aprovada cedo,
poucos fatos, métodos ausentes, texto curto, score alto apesar de blockers e risco
de custo incorreto após trocar o nome do modelo na interface.

## Mudanças

- Campo `required_methods` no briefing V3 e validação mínima para guias procedurais.
- Pesquisa específica por método e correspondência determinística de nomes, aliases
  e variações equivalentes.
- Mínimo configurável de claims e passos por método antes da redação.
- Writer bloqueado antes da chamada cara quando faltam dossiês, evidências ou links.
- Blocos do artigo vinculados a `method_id`, garantindo seção própria e sequência
  completa para cada método.
- Faixa mínima de palavras adaptada ao número de métodos e seções.
- Uma reparação estrutural integral recebe a lista exata de falhas; não há loop
  ilimitado de regenerações.
- Score limitado a 59% na presença de qualquer blocker crítico.
- Catálogo de modelos OpenAI conhecidos: ao salvar, preços, timeout, retries e
  limites de saída são atualizados no servidor, evitando herdar a tarifa do modelo
  anterior.
- Writer configurado com teto de 12.000 tokens, compatível com artigo procedural
  extenso e com o limite recomendado de US$ 0,40 por chamada.
- Migration `0030` corrige rotas já salvas que correspondam exatamente aos modelos
  e papéis recomendados, sem alterar rotas personalizadas.

## Variáveis recomendadas para o canário

```env
MAX_PIPELINE_COST_USD=0.80
MAX_AGENT_COST_USD=0.40
V3_MIN_APPROVED_CLAIMS=18
V3_MIN_CLAIMS_PER_METHOD=3
V3_MIN_STEPS_PER_METHOD=3
V3_WRITER_REPAIR_ATTEMPTS=1
V3_MIN_WORD_COUNT=1800
V3_MAX_WORD_COUNT=3500
```

## Roteamento recomendado

- Planner: `gpt-5-mini`
- Researcher: `gpt-5-mini`
- Research Gatekeeper: `gpt-5.4-mini`
- Writer: `gpt-5.4`
- Editor: `gpt-5.4-mini`
- Skill Curator: `gpt-5-mini`

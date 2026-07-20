# Validação — Editorial V3.5.1 Generation Hardening

Data: 20/07/2026

## Resultado resumido

| Camada | Resultado |
|---|---:|
| Backend — pytest completo | 890 aprovados, 40 ignorados por pré-condições de ambiente |
| Backend — Ruff | aprovado |
| Backend — compileall | aprovado |
| Frontend — Vitest | 67 aprovados |
| Frontend — ESLint | aprovado |
| Frontend — TypeScript/Vite build | aprovado |
| Migration head esperado | `0033` |

## Testes específicos adicionados

`backend/tests/test_editorial_v3_generation_hardening.py` cobre:

1. citação contínua/ordenada aprovada;
2. palavras embaralhadas rejeitadas;
3. slug com acentos transliterado sem colisão por remoção;
4. revisão preservando números e negação;
5. envelope real de `task_data` e rejeição de segredo;
6. dimensionamento do fact-checker por sentenças factuais;
7. tabelas estruturadas e retangulares;
8. callouts tipados;
9. fact-check `passed` inconsistente rejeitado;
10. remoção de instruções ocultas no HTML;
11. filtro de instruções em fragmentos de fonte;
12. detecção de idioma incompatível;
13. similaridade e cobertura direcional de keyword.

A suíte existente também foi ajustada para validar:

- head de migration `0033`;
- writer com 24.000 tokens nas rotas OpenAI selecionadas;
- revisores com rotas próprias;
- política de pesquisa V3.5 preservada;
- novo estado e detalhe de projeto;
- skills superiores versão 2.1.0;
- orçamento e retomada de pesquisa.

## Comandos executados

Backend:

```bash
cd backend
ruff check app tests
python -m compileall -q app
pytest -q
```

Frontend:

```bash
cd frontend
npm test -- --run
npm run lint
npm run build
```

## O que não foi validado com infraestrutura externa

Não foram utilizadas chaves reais de OpenAI, Anthropic, Gemini, Tavily ou Serper. Portanto, a validação confirma contratos, payloads, gates e adaptadores por testes automatizados, mas não substitui um smoke test de staging com cobrança real.

Os 40 testes ignorados dependem de serviços ou variáveis não disponíveis neste ambiente, incluindo PostgreSQL/pgvector real, Redis/Celery e integrações operacionais. A migration `0033` foi validada estruturalmente e pela suíte, mas deve ser executada em staging sobre uma cópia do banco antes do rollout.

## Testes obrigatórios em staging

1. aplicar `alembic upgrade head` e confirmar `0033`;
2. criar projeto novo em `pt-BR`, `en-US` e `es-ES`;
3. capturar request real de cada papel e confirmar presença do sentinel no `task_data`;
4. confirmar ausência de credenciais no request, logs e AgentRun;
5. gerar artigo com tabela e callout e conferir persistência após reload;
6. induzir fact-check incompleto e confirmar bloqueio;
7. alterar número na edição de linguagem e confirmar novo fact-check/bloqueio;
8. tentar duplicar keyword/artigo existente e conferir o gate de canibalização;
9. bloquear um run e confirmar que o artigo final anterior não foi sobrescrito;
10. aprovar humanamente e verificar promoção final e referências visíveis.

## Critério de liberação

A V3.5.1 pode entrar em canário somente quando os dez testes de staging passarem e houver revisão humana de múltiplos artigos. O pacote não declara que os provedores externos foram exercitados neste ambiente.

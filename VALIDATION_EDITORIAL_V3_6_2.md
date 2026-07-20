# Validação — Editorial V3.6.2

Data: 20/07/2026

## Resultado automatizado

| Verificação | Resultado |
|---|---:|
| Testes backend coletados | 1.013 |
| Testes backend aprovados | 973 |
| Testes backend ignorados por infraestrutura externa | 40 |
| Testes dedicados de confiabilidade V3.6.2 | 43 aprovados |
| Testes frontend | 72 aprovados |
| Ruff | aprovado |
| Python compileall | aprovado |
| ESLint | aprovado |
| TypeScript | aprovado |
| Build Vite de produção | aprovado |
| Alembic head | `0035` |

A suíte backend foi executada em quatro grupos para evitar o limite do executor:

```text
322 passed, 3 skipped
290 passed
217 passed, 35 skipped
144 passed, 2 skipped
```

## Regressões cobertas

- conjunto correto de papéis por V2/V3;
- bootstrap seguro e com custos para OpenAI, Gemini e Anthropic;
- diagnóstico de manifesto com dependências específicas;
- commit de reparações de rota;
- readiness orientada à versão em execução;
- retomada de manifesto fixado sem depender do inventário mutável atual;
- comparação de payload de idempotência;
- broker indisponível convertido em retry durável;
- campanha MSB preenchida integralmente;
- preflight bloqueando antes de criar projeto órfão;
- projeto V2 e V3 retornando run ID;
- reutilização da mesma chave quando a resposta é perdida;
- início manual de projeto legado sem run;
- mensagens de erro com códigos e dependências.

## Limites da validação local

Não foram realizadas chamadas pagas reais a OpenAI, Gemini, Anthropic, Tavily ou
Serper. Os 40 testes ignorados dependem de componentes externos ou ambientes de
integração específicos. A validação de produção ainda precisa comprovar:

1. publicação real no Redis/Celery;
2. retry do Beat durante indisponibilidade temporária;
3. execução V2 e V3 completa com provedores reais;
4. comportamento com o PostgreSQL de produção e os dados existentes;
5. orçamento, latência e limites reais das APIs.

Nenhum software sério pode prometer ausência matemática de qualquer erro futuro.
Esta versão remove e testa os modos de falha identificados, evita perda
silenciosa de execução e adiciona diagnóstico e recuperação para falhas
operacionais remanescentes.

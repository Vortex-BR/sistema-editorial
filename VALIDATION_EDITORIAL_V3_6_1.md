# Validação — Editorial V3.6.1

Data: 20/07/2026

## Ambiente desta validação

A validação foi executada localmente sobre o código atualizado, sem usar chaves de
OpenAI, Anthropic, Gemini, Tavily ou Serper e sem conectar a PostgreSQL, Redis ou
Celery reais.

## Resultados

| Verificação | Resultado |
|---|---:|
| Compilação Python (`compileall`) | Aprovada |
| Ruff em `app` e `tests` | Aprovado |
| Alembic heads | `0035 (head)` |
| Testes backend coletados | 970 |
| Testes backend aprovados | **930** |
| Testes backend ignorados por condição | **40** |
| Testes frontend | **67 aprovados** |
| ESLint | Aprovado |
| TypeScript | Aprovado |
| Build Vite de produção | Aprovado |

A suíte backend foi executada em três grupos determinísticos para respeitar o
limite do runner. A soma cobre os 970 testes coletados:

- grupo 1: 266 aprovados, 3 ignorados;
- grupo 2: 181 aprovados;
- grupo 3: 483 aprovados, 37 ignorados.

## Testes novos da V3.6.1

O arquivo dedicado da V3.6.1 contém **25 testes focados de regressão**. Além disso, a suíte de exportação ganhou uma regressão específica para sanitização do vínculo final. Os testes cobrem:

1. claim irrelevante da mesma seção não cobrir pergunta crítica;
2. draft bloquear quando não responde às perguntas críticas;
3. canonicalização de IDs distintos pelo mesmo `support_group`;
4. preservação de claim disputado como contexto/conflito;
5. reserva de queries de inteligência com seis slots antigos ocupados;
6. proibição específica de seção;
7. rejeição de referência cruzada entre seções;
8. detecção factual de frases declarativas sem número;
9. compactação de contexto preservando o claim usado;
10. roteamento do Intelligence Gate para recuperação;
11. vínculo do estado ao hash exato do draft;
12. fact-check de frases duplicadas por `sentence_id`;
13. rejeição de suporte composto por claims independentes;
14. retorno da recuperação bem-sucedida ao `source_reader`;
15. repetição controlada quando a recuperação ainda não encontrou candidatos;
16. bloqueio de contexto irredutível sem truncar o draft;
17. exigência de linguagem explícita ao usar claim conflitado;
18. preservação do `canonical_claim_id` persistido em registros migrados;
19. canonicalização estável de grupos com acentos e normalização Unicode;
20. redução de falsos positivos factuais em conectivos e transições editoriais;
21. manutenção da detecção para comparações factuais explícitas;
22. aplicação consistente do override `node_resolution.research_required`;
23. binding final seguro no relatório de fontes;
24. rejeição de contexto factual irredutível sem corte silencioso;
25. revalidação após recuperação e mutações de revisão.

## Build frontend

O frontend foi reinstalado com `npm ci` e validado com:

```text
npm test
npm run lint
npm run build
```

O build resultou em bundle de produção válido. `node_modules`, `dist` e o arquivo
incremental do TypeScript não fazem parte do ZIP final.

## O que esta validação não comprova

Ainda precisa ser validado em staging:

- `alembic upgrade head` e rollback em clone do PostgreSQL 17 + pgvector;
- FK e backfill da migration com volume de dados real;
- Redis, broker, Worker, Beat, leases, retries e resume;
- respostas reais e limites de contexto dos modelos configurados;
- Tavily/Serper, circuit breaker e recuperação sem mocks;
- custos, latência e concorrência;
- qualidade semântica por revisão humana em múltiplos nichos e idiomas;
- calibração do limiar de alinhamento e futuro classificador NLI.

## Checklist de staging obrigatório

1. Fazer backup e restaurar uma cópia isolada do banco.
2. Executar `alembic upgrade head` e confirmar `0035`.
3. Verificar quantidade de `canonical_claim_id` nulos: deve ser zero.
4. Verificar quantidade de `logical_sentence_id` nulos: deve ser zero.
5. Executar um run novo que passe sem recuperação.
6. Executar um run com pergunta crítica inicialmente sem evidência e confirmar o
   loop de recuperação.
7. Interromper um run durante uma revisão e confirmar que o lifecycle permanece
   `draft_pending_validation` até nova validação.
8. Alterar deliberadamente uma sentença após a validação e confirmar bloqueio por
   hash divergente.
9. Confirmar que um conflito aparece no grafo sem ser liberado como conclusão.
10. Comparar o artigo final e seu `article_version_id` com o snapshot aprovado.

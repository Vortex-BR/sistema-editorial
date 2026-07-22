# Editorial V3.5.1 — Generation Hardening

## Propósito

Este documento descreve as garantias técnicas da V3.5.1 entre a entrada do usuário e a publicação. A versão não tenta tornar o modelo “inteligente” por quantidade de agentes; ela impede que o pipeline perca contexto, aceite autodeclarações inconsistentes ou publique um candidato que não passou pelos gates.

## Fronteiras de confiança

### Instruções

Prompts superiores, skills fixadas no manifesto e instruções do estágio são confiáveis e versionados.

### Dados públicos da tarefa

Briefing, contrato, claims, dossiês, rascunhos e fragmentos de fonte entram em `<untrusted_task_data>`. Eles são necessários para a tarefa, mas não podem redefinir regras do agente.

### Dados privados

Credenciais, tokens, URLs internas e metadados de auditoria permanecem fora do prompt. Qualquer chave suspeita em `task_data` bloqueia a chamada.

## Contrato do AgentRuntime

```python
await runtime.call(
    role="writer",
    prompt="Produza o rascunho conforme as instruções.",
    trace_input={"run_id": "...", "stage": "writer"},
    task_data={"generation_brief": {...}, "claim_catalog": [...]},
    response_model=V3StructuredDraft,
)
```

A idempotência usa o hash do payload público. Um mesmo prompt com claims ou briefing diferentes não reutiliza resultado antigo.

## GenerationBrief

O brief canônico é a única fonte de verdade para:

- idioma;
- objetivo e promessa;
- público e nível de conhecimento;
- keyword e termos secundários;
- marca, tom e exemplos aprovados;
- oferta, CTA e ponte comercial;
- mínimo/máximo de palavras;
- H2/H3 e seções obrigatórias;
- fontes preferidas/proibidas e idade máxima;
- claims proibidos;
- link interno;
- contexto e limites de escopo.

Os campos não são apenas exibidos no prompt. Eles geram diagnósticos determinísticos no rascunho e no candidato final.

## Evidência e factualidade

Uma sentença entra no conjunto factual quando o modelo marca `is_factual` **ou** quando regras conservadoras identificam números, unidades, negação, causalidade, comparações, requisitos, segurança, eficácia ou recomendações.

Para cada sentença factual:

1. deve existir check do fact-checker;
2. o texto e o bloco devem corresponder ao rascunho;
3. os claims citados devem existir no catálogo permitido;
4. negação e valores precisam ser compatíveis;
5. o suporte lexical/semântico mínimo deve estar presente;
6. `unsupported` ou `contradicted` impede `passed`;
7. uma edição posterior força nova verificação.

A camada determinística é deliberadamente conservadora. Ela não substitui o futuro grafo de evidências, mas impede que o próprio modelo desative as verificações.

## Citações

A citação é verificada por:

- substring normalizada contínua; ou
- janela local com tokens na mesma ordem e tolerância limitada.

A presença das mesmas palavras em posições arbitrárias não é suficiente.

## Estrutura do artigo

Blocos aceitos:

- heading;
- paragraph;
- list;
- table;
- callout.

Tabelas mantêm headers e linhas tipadas. Callouts mantêm tipo, título e corpo. Revisões locais não podem alterar a forma estrutural do bloco. O Markdown final inclui tabelas reais, callouts e referências numéricas.

## Ordem dos gates

```text
writer
→ draft diagnostics
→ repair (limitado)
→ development review
→ fact-check
→ language review
→ post-language fact-check
→ final brief/SEO/language diagnostics
→ similarity/cannibalization
→ quality gate
→ final promotion
→ human review package
```

Persistir um `ArticleVersion` candidato não equivale a publicar. Somente a etapa de promoção grava o artigo final.

## Configurações novas ou relevantes

```env
AGENT_TASK_DATA_MAX_CHARACTERS=400000
CONTENT_SIMILARITY_WARNING_THRESHOLD=0.72
CONTENT_DUPLICATE_THRESHOLD=0.90
```

Os limites de custo continuam obrigatórios. Rotas de writer e revisores precisam suportar o output definido no catálogo.

## Rollout

1. aplicar migration `0033`;
2. publicar imagem imutável;
3. manter V3 em canário;
4. iniciar runs novos;
5. executar smoke tests com provedores reais;
6. revisar artigos às cegas;
7. liberar gradualmente.

Não retome um checkpoint V3.5 como V3.5.1.

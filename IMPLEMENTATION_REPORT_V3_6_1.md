# Relatório de implementação — Editorial V3.6.1

## 1. Escopo

A implementação foi executada sobre o pacote
`docker-seo-v3.6-editorial-intelligence-core.zip` e orientada pela auditoria
`REVISAO_TECNICA_V3_6_BUGS_FLUXOS.md`.

A meta foi fechar a cadeia:

```text
pergunta editorial
→ claim canônico e realmente pertinente
→ fonte e fato persistidos
→ sentença identificável
→ resposta verificável
→ revisão que invalida o estado anterior
→ hash exato aprovado
→ promoção final
```

## 2. Modelo de dados

### Claim canônico

`V3KnowledgeClaimRecord` agora possui `canonical_claim_id`. Novos registros usam
um UUID determinístico por run e `support_group`; registros antigos recebem um ID
estável durante a migration. O repositório agrega registros de fonte no claim
canônico e mantém as arestas para cada fonte/fato original.

A canonicalização é conservadora. Um grupo só é utilizável quando seção, papel de
evidência e `support_group` permanecem coerentes. Incompatibilidades são mantidas
para auditoria, mas não são liberadas ao Writer.

### Identidade lógica de sentença

`V3DraftSentence` recebe `sentence_id`. Na persistência, esse ID é gravado como
`sentence_claims.logical_sentence_id`. A restrição é por bloco, permitindo que a
mesma sentença lógica seja preservada em versões imutáveis diferentes do artigo
sem colisão global.

### Snapshot ligado ao artefato

`EditorialIntelligenceSnapshot` passa a registrar o hash SHA-256 do draft
validado, a versão do artigo e a revisão do draft. O lifecycle inclui
`draft_pending_validation`.

## 3. Evidence Graph V1.1

O grafo agora contém:

- `source_claim_ids` e `support_group` por claim canônico;
- `question_coverage` com score, autorização, compatibilidade de papel e motivo;
- `question_claim_map` apenas com edges semanticamente suportados;
- conflitos preservados mesmo quando o claim não pode ser escrito diretamente;
- validação fechada de ownership entre seção, pergunta, claim e conflito.

Não existe mais fallback “mesma seção = resposta”. O limiar lexical/semântico
atual é um piso determinístico; ele deve ser calibrado e complementado por NLI em
staging antes de autonomia editorial plena.

## 4. Answer Map e validação do draft

Cada sentença pode declarar as perguntas que responde. O motor recalcula e valida:

- se a pergunta pertence à seção do bloco;
- se os claims da sentença estão mapeados à pergunta;
- se a resposta é direta, parcial, contextual ou ausente;
- se o texto satisfaz a pergunta ou seu sinal de conclusão;
- se condições, limitações e conflitos foram preservados;
- se conclusões proibidas globais ou específicas da seção aparecem;
- se toda afirmação factual possui suporte atômico individual.

Perguntas editoriais que não exigem pesquisa podem ser respondidas explicitamente
sem claim, mas perguntas críticas factuais continuam exigindo evidência.

## 5. Recuperação orientada pelas lacunas

O Intelligence Gate converte blockers recuperáveis em tarefas associadas ao
`question_id` e às tarefas de pesquisa da seção. O grafo encaminha o run para
`targeted_source_recovery`, preservando os budgets e circuit breakers existentes.

Quando novas fontes são encontradas:

```text
targeted_source_recovery
→ source_reader
→ source_coverage_gate
→ knowledge_synthesizer
→ evidence_graph_builder
→ intelligence_gate
```

Quando não há novas fontes e os limites são atingidos, o run bloqueia com código
específico e sem loop infinito.

## 6. Writer e revisores

O Writer recebe:

- plano de perguntas por seção;
- claims autorizados para cada pergunta;
- políticas `direct`, `conditional`, `context_only` e `prohibited`;
- condições, limitações e conflitos;
- instruções para sentenças factuais atômicas;
- IDs estáveis de sentença e pergunta.

Development Editor, Fact Checker e Language Editor usam o mesmo estado canônico.
Antes e depois de cada mutação, o draft é marcado como pendente e revalidado. O
fact-check identifica sentenças por UUID, eliminando colisões por texto duplicado.

## 7. Context budget

`ContextBudgetPlanner` mede o payload serializado antes das chamadas. Ele remove
representações redundantes nesta ordem controlada:

1. claims não utilizados pelo mapa pergunta→evidência;
2. campos não essenciais de referências externas;
3. campos do contrato que não governam a etapa;
4. redundâncias nos dossiês de seção e método;
5. diagnósticos e checks anteriores não essenciais em revisões.

O draft e os textos factuais nunca são cortados no meio. Se a compactação segura
não for suficiente, o estágio bloqueia com diagnóstico de tamanho e passos
aplicados.

## 8. Quality Gate e rastreabilidade

O conteúdo candidato só pode ser promovido quando:

- o estado está `draft_validated`;
- o hash recalculado é idêntico a `validated_artifact_hash`;
- `article_version_id` aponta para a versão candidata atual;
- não houve mutação posterior sem revalidação.

O relatório de fontes passa a expor a identidade lógica de cada sentença, as
perguntas respondidas e o status da resposta, além dos claims e fontes.

## 9. Segurança e limites mantidos

A implementação preserva:

- isolamento de credenciais;
- proteção de contexto introduzida na V3.5.1;
- limites de requests, retries, fetches, créditos e tempo da pesquisa V3.5;
- leitura segura e política SSRF;
- revisão humana final obrigatória;
- V2 disponível como pipeline paralelo.

## 10. Itens deliberadamente não implementados nesta versão

A V3.6.1 corrige integridade do fluxo, mas não conclui toda a evolução semântica:

1. classificador NLI independente e calibrado por idioma;
2. interface visual de Evidence Graph e override humano;
3. resolução humana formal de conflitos;
4. orçamento real por tokens do tokenizer de cada provedor — o planner atual usa
   caracteres como limite conservador;
5. benchmark com chamadas reais de LLM e busca;
6. validação de migration em cópia real do PostgreSQL de produção;
7. teste E2E distribuído com Redis/Celery e retomada após crash.

Esses itens permanecem obrigatórios antes de liberar produção editorial autônoma
em lote.
## 11. Ajustes finais incorporados antes do release

Antes do empacotamento final também foram corrigidos quatro pontos de regressão:

- identidade canônica preservada para claims já migrados e normalização Unicode do
  grupo de suporte;
- falsos positivos do detector factual em conectivos e chamadas editoriais;
- aplicação uniforme do override de pesquisa definido por `node_resolution`;
- atualização do `source_report` somente depois do binding final do Quality Gate,
  garantindo que hash, versão e revisão correspondam ao artefato promovido.

A exportação foi coberta por teste adicional para garantir que apenas dados seguros
do binding e da rastreabilidade sejam expostos.


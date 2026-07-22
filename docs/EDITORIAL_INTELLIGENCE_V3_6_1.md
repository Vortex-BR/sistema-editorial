# Editorial Intelligence V3.6.1 — integridade de fluxo

## Princípio central

A V3.6.1 trata uma resposta editorial como uma cadeia auditável, e não apenas como
texto gerado:

```text
Question
→ QuestionClaimCoverage
→ Canonical Claim
→ Source Fact
→ Source Document
→ Draft Sentence
→ QuestionAnswerRecord
→ Validated Artifact Hash
→ Article Version
```

Qualquer aresta ausente, cruzada para outra seção ou incompatível bloqueia o uso
direto do claim ou a promoção do artigo.

## Estados do lifecycle

```text
planned
→ evidence_attached
→ writer_ready
→ draft_pending_validation
→ draft_validated
```

`blocked` pode ocorrer em qualquer gate. Uma revisão nunca mantém automaticamente
o status `draft_validated`: toda mutação retorna a `draft_pending_validation`.

## Critérios para pergunta crítica

Uma pergunta crítica que exige pesquisa só é considerada coberta quando existe um
edge com:

- `status=semantically_supported` ou override humano futuro;
- claim autorizado na seção;
- papel de evidência compatível;
- fonte e fato-fonte persistidos;
- política de escrita direta ou condicional;
- score mínimo de alinhamento.

Um claim apenas pertencer à mesma seção não é suficiente.

## Claims canônicos

Cada registro extraído de uma fonte permanece auditável, mas claims equivalentes
são agrupados em um nó canônico. A força do claim usa fontes independentes do grupo,
sem duplicar o conteúdo enviado ao Writer.

Quando registros do mesmo grupo discordam em seção, função ou sentido, o grupo
recebe `integrity_issues` e não é usado diretamente.

## Conflitos

Claims `disputed`, `insufficient_evidence`, condicionais ou com `conflict_group`
podem entrar no Evidence Graph mesmo sem aprovação para escrita direta. A política
resultante controla o uso:

- `direct`: conclusão sustentada;
- `conditional`: exige condição/limitação;
- `context_only`: somente para explicar incerteza ou divergência;
- `prohibited`: não pode sustentar texto factual.

## Recuperação

Blockers recuperáveis geram queries associadas à pergunta e à tarefa de pesquisa da
seção. O sistema compartilha os budgets e circuit breakers já existentes e limita a
duas rodadas de recuperação editorial.

O loop não ignora o restante do pipeline: novas fontes voltam à leitura, cobertura,
síntese e reconstrução do grafo antes de um novo gate.

## Contrato das sentenças

Cada sentença possui:

```json
{
  "sentence_id": "UUID",
  "text": "...",
  "is_factual": true,
  "evidence": [{"claim_id": "UUID"}],
  "question_ids": ["q_section_central_1"],
  "answer_status": "direct"
}
```

O fact-check precisa devolver os mesmos IDs, texto e claims. Sentenças factuais
complexas devem ser divididas para que cada proposição seja sustentada por um claim
individual.

## Vínculo de aprovação

O hash usa a representação canônica do draft. O Quality Gate recalcula o hash do
artefato atual e compara com o snapshot `draft_validated`. A versão candidata do
artigo também deve ser a mesma registrada no estado.


## Garantias adicionais do release

- IDs canônicos sobrevivem à migration e não são recalculados de forma divergente
  durante a leitura do grafo.
- Normalização Unicode evita grupos canônicos diferentes para o mesmo conceito.
- Conectivos e CTAs editoriais não são tratados como afirmações factuais apenas por
  conterem palavras curtas ambíguas.
- Overrides de pesquisa do contrato são propagados para seção e perguntas.
- O relatório final de fontes é atualizado após o vínculo definitivo do Quality
  Gate, e a exportação expõe apenas os campos seguros dessa ligação.

## Operação

Depois do deploy:

```bash
alembic upgrade head
alembic heads
```

Resultado esperado:

```text
0035 (head)
```

Crie um run novo. Não retome checkpoints da V3.6 ou anteriores como V3.6.1.

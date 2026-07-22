# Revisão técnica do Editorial V3.6 — bugs, falhas e riscos dos fluxos executados

**Artefato revisado:** `docker-seo-v3.6-editorial-intelligence-core.zip`  
**Escopo:** backend do Pipeline Editorial V3, Motor de Inteligência Editorial, pesquisa, síntese, Evidence Graph, Writer, revisores, fact-check, snapshots e Quality Gate.  
**Natureza desta entrega:** auditoria e plano de correção. Nenhum arquivo do sistema foi alterado nesta etapa.

---

## 1. Veredito executivo

A V3.6 introduziu uma base útil — estado canônico, perguntas editoriais, Evidence Graph, validações antes e depois da redação e snapshots —, mas **o motor ainda não garante que o conteúdo responda às perguntas planejadas nem que a evidência correta sustente cada resposta**.

O problema mais grave é uma desconexão entre quatro camadas:

```text
pergunta editorial
→ claim realmente capaz de respondê-la
→ frase escrita no artigo
→ validação de que a resposta foi entregue
```

Hoje, o pipeline pode:

1. considerar uma pergunta crítica coberta por um claim apenas porque ambos pertencem à mesma seção;
2. liberar o Writer mesmo com alinhamento fraco;
3. aprovar um rascunho que não responde às perguntas críticas;
4. perder a corroboração entre fontes independentes;
5. eliminar evidências disputadas antes de construir o grafo de conflitos;
6. bloquear definitivamente em vez de executar uma recuperação orientada pelas lacunas descobertas pelo próprio motor.

**Conclusão:** a V3.6 não deve ser considerada pronta para produção editorial autônoma. A recomendação é criar uma **V3.6.1 de correção estrutural**, sem adicionar novos agentes, antes de evoluir para novos recursos de inteligência.

---

## 2. Metodologia utilizada

A revisão combinou:

- inspeção estática dos serviços, schemas, executor, grafo e repositórios;
- compilação de todo o backend;
- Ruff em `app` e `tests`;
- inspeção da cadeia Alembic;
- 105 testes direcionados aos fluxos editoriais e de pesquisa;
- sete provas executáveis específicas para hipóteses não cobertas pela suíte atual;
- tentativa de execução da suíte completa e dos testes frontend.

### Resultado independente

| Verificação | Resultado |
|---|---:|
| `python -m compileall -q app` | Aprovado |
| `ruff check app tests` | Aprovado |
| `alembic heads` | `0034 (head)` |
| Testes backend direcionados | **105 aprovados em 15,13 s** |
| Suíte backend completa | Não concluída; atingiu o limite de tempo por volta de 45%, sem falhas observadas antes do timeout |
| Testes frontend | Não executados; o pacote não contém `node_modules` e `vitest` não estava instalado |
| PostgreSQL/Redis/Celery reais | Não disponíveis nesta auditoria |
| OpenAI/Anthropic/Gemini/Tavily/Serper reais | Não utilizados |

Os testes existentes aprovarem não invalida os bugs encontrados: vários deles estão fora das asserções atuais ou são mascarados por fixtures que não reproduzem os IDs gerados pelo fluxo real.

---

# 3. Falhas críticas — P0

## P0-01 — Perguntas críticas são consideradas cobertas por evidência irrelevante

### Evidência no código

Em `backend/app/services/editorial_v3/content_intelligence.py:479-511`, cada pergunta recebe **todos os claims da mesma seção** que tenham papel de evidência compatível. Se não houver papel compatível, ocorre fallback para todos os claims da seção. O ranking lexical muda apenas a ordem; não elimina claims irrelevantes.

Em `content_intelligence.py:665-700`, a pergunta é considerada coberta quando existe qualquer claim não proibido. Alinhamento baixo gera apenas warning, não bloqueio.

Além disso, a cobertura não exige que o claim esteja na lista final `allowed_claim_ids` da seção produzida pelo dossier. Portanto, um claim excluído do plano de escrita ainda pode ser contado como cobertura da pergunta.

### Prova executável

Foi criada uma pergunta sobre **temperatura crítica exata**, enquanto os únicos claims diziam que folhas possuíam pigmentação verde.

Resultado observado:

```text
writer_readiness = passed
blockers = []
```

O sistema apenas emitiu warnings de alinhamento fraco. A menor pontuação foi `0.1429`, acima do limiar de warning de `0.08` em alguns casos por sobreposição de palavras genéricas.

### Impacto

- o gate transmite uma falsa sensação de completude;
- a redação começa sem resposta real para perguntas centrais;
- conteúdos podem ser bem estruturados, porém vazios ou desviados da intenção;
- o score de cobertura se torna quantitativo, não semântico.

### Correção obrigatória

Criar um vínculo de cobertura com status explícito:

```text
unsupported | candidate | semantically_supported | human_overridden
```

Uma pergunta crítica só deve contar como coberta quando:

1. o claim estiver autorizado na seção;
2. o papel de evidência for compatível;
3. houver score semântico acima do limiar calibrado;
4. houver pelo menos um trecho-fonte relacionado ao claim;
5. a conclusão/condição do claim for compatível com a pergunta.

Não deve existir fallback silencioso para todos os claims da seção.

---

## P0-02 — O rascunho pode passar sem responder às perguntas críticas

### Evidência no código

`ContentIntelligenceEngine.validate_draft`, em `content_intelligence.py:786-958`, verifica:

- frases factuais;
- presença de claim;
- uso na seção correta;
- suporte lexical;
- condições e linguagem de incerteza;
- ao menos uma frase factual por seção de pesquisa;
- conclusões proibidas globais.

Porém, **não verifica quais perguntas foram respondidas por quais frases**. Não há `question_id` nas sentenças, nem cálculo de cobertura pergunta→frase.

### Prova executável

O rascunho usou corretamente os claims sobre pigmentação verde, mas não mencionou a temperatura solicitada pelas perguntas críticas.

Resultado:

```text
draft_validation = passed
blockers = []
warnings = []
```

### Impacto

O artigo pode cumprir formato, evidência e seções, mas omitir justamente as respostas que justificaram a pesquisa e o planejamento.

### Correção obrigatória

Adicionar um `answer_map` ao rascunho:

```json
{
  "question_id": "q_analysis_central_1",
  "sentence_ids": ["..."],
  "claim_ids": ["..."],
  "answer_status": "direct|partial|contextual|unanswered"
}
```

A validação deve recalcular esse mapa independentemente do Writer e bloquear:

- pergunta crítica sem resposta;
- resposta baseada em claim não mapeado à pergunta;
- resposta parcial apresentada como completa;
- conclusão que não satisfaz o `completion_signal`.

---

## P0-03 — A corroboração real entre fontes independentes não é agregada

### Evidência no fluxo real

O extrator cria um `claim_key` diferente por URL, adicionando um sufixo derivado da fonte em `backend/app/orchestration/v3/executor.py:2859-2864`. O `support_group` é o campo que representa equivalência semântica entre claims de fontes diferentes.

Entretanto, `ArtifactRepository.knowledge_claims`, em `artifact_repository.py:426-450`, converte cada registro em `KnowledgeClaim` usando:

```python
claim_id = row.fact_id or row.id
```

O `support_group` não é repassado.

Depois, `ContentIntelligenceEngine.attach_evidence`, em `content_intelligence.py:335-341`, agrega exclusivamente por `claim_id`.

Como cada fonte normalmente gera um `FactLedger.id` diferente, dois registros do mesmo `support_group` chegam ao motor com IDs diferentes e permanecem como dois claims isolados.

### Prova executável

Dois claims com:

- mesmo texto;
- mesmo `support_group`;
- duas fontes independentes;
- IDs reais distintos;

produziram:

```text
claim_nodes = 2
source_count_por_claim = [1, 1]
```

### Teste enganoso atual

`backend/tests/test_editorial_intelligence_core.py` testa agregação fornecendo manualmente **o mesmo `claim_id` nas duas linhas**. Essa situação não reproduz o fluxo real, que cria IDs por fato/fonte.

### Impacto

- fontes independentes não elevam corretamente a força de um claim;
- o Writer recebe claims duplicados;
- métricas de diversidade e confiança ficam incorretas;
- conflitos e corroboração podem ser confundidos;
- a promessa central do Evidence Graph fica incompleta.

### Correção obrigatória

Introduzir um identificador canônico persistente, por exemplo `canonical_claim_id`, derivado e congelado após a validação do `support_group`.

```text
source claim records (N)
→ canonical claim (1)
→ source fact edges (N)
```

O grafo deve agrupar por `canonical_claim_id`, nunca por `FactLedger.id` nem por registro de fonte.

---

## P0-04 — Evidência disputada é removida antes do grafo de conflitos

### Evidência no código

Em `artifact_repository.py:333-344`, qualquer bundle que contenha `disputed` ou `insufficient_evidence` recebe `valid_conclusion = False` e não é aprovado.

Em `executor.py:1242-1250`, o executor carrega somente:

```python
knowledge_claims(approved_only=True)
```

O Evidence Graph é construído depois, usando apenas essa coleção aprovada. Portanto, os claims disputados que deveriam alimentar `EvidenceConflictNode` podem desaparecer antes de o motor conhecê-los.

### Impacto

- o sistema não representa divergência real;
- o Writer recebe uma versão artificialmente “limpa” do conhecimento;
- não há como explicar controvérsias ou limites de consenso;
- o conflito pode virar ausência de evidência, em vez de conflito documentado.

### Correção obrigatória

Separar dois conceitos:

```text
approved_for_direct_writing
eligible_for_intelligence_graph
```

Claims disputados devem entrar no grafo com política `context_only` ou `prohibited`, preservando fontes e razões. Apenas a redação direta deve ser proibida.

---

## P0-05 — O Intelligence Gate bloqueia em vez de recuperar lacunas descobertas

### Evidência no fluxo

Em `backend/app/orchestration/v3/graph.py:157-176`, o fluxo é:

```text
evidence_graph_builder → intelligence_gate
```

Se o relatório não passa, o graph encerra a execução com `V3_INTELLIGENCE_GATE_BLOCKED`.

Não há transição do Intelligence Gate para `targeted_source_recovery`, mesmo quando o blocker é recuperável, como:

- pergunta crítica sem claim;
- claim sem fonte independente suficiente;
- papel de evidência ausente;
- seção com cobertura incompleta.

### Impacto

O motor detecta corretamente uma lacuna, mas não usa essa inteligência para pesquisar melhor. O usuário recebe bloqueio definitivo apesar de ainda haver orçamento e provedores disponíveis.

### Correção obrigatória

Classificar blockers como:

```text
recoverable | contract_error | nonrecoverable | budget_exhausted
```

Fluxo esperado:

```text
intelligence_gate failed/recoverable
→ gerar RecoveryTask por question_id
→ targeted_source_recovery
→ source_reader
→ synthesis incremental
→ rebuild evidence graph
→ intelligence_gate
```

Usar limite de rodadas, custo e tempo para evitar loop infinito.

---

# 4. Falhas altas — P1

## P1-01 — O Writer não recebe o mapa pergunta→claim

`content_intelligence.py:1029-1056` envia perguntas, planos de seção, políticas e conflitos, mas omite:

- `question_claim_map`;
- `question_alignment_scores`;
- status de cobertura por pergunta;
- trechos-fonte que justificam a ligação.

O Writer recebe as peças, mas não a conexão central do motor. Ele precisa inferir novamente qual claim responde a cada pergunta.

**Correção:** incluir um `question_evidence_plan` compacto, ordenado por seção e criticidade.

---

## P1-02 — Consultas de inteligência são descartadas quando a tarefa já possui seis queries

Em `content_intelligence.py:994-1000`, as queries são montadas assim:

```python
[*task.queries, *intelligence_queries][:6]
```

As consultas originais vêm primeiro. Se já houver seis, nenhuma consulta baseada nas perguntas canônicas entra no plano.

### Prova executável

Uma tarefa com seis queries originais permaneceu exatamente com as mesmas seis após `augment_research_plan`. Nenhuma pergunta canônica foi incorporada, embora a rationale afirmasse que o plano estava vinculado ao mapa editorial.

**Correção:** reservar slots obrigatórios por função, por exemplo:

```text
2 queries base + 2 queries por pergunta crítica + 1 conflito + 1 recuperação
```

ou substituir queries menos informativas por score, em vez de simplesmente concatenar e cortar.

---

## P1-03 — Proibições específicas de seção não são aplicadas

O plano de seção possui `prohibited_conclusions` e `required_conditions`, mas `validate_draft` verifica apenas `state.prohibited_claims` globalmente em `content_intelligence.py:920-935`.

### Prova executável

Foi adicionada a proibição “frase proibida apenas nesta seção” ao plano de `analysis`, e a frase foi inserida nessa seção. O resultado foi:

```text
draft_validation = passed
```

**Correção:** validar cada bloco contra as regras da sua seção e registrar a regra violada no finding.

---

## P1-04 — O schema aceita relações cruzadas semanticamente inválidas

`ContentIntelligenceState.state_references_are_closed`, em `backend/app/schemas/editorial_intelligence.py:204-227`, confirma apenas que os IDs existem.

Ele não confirma que:

- uma pergunta listada em `section.question_ids` pertence àquela seção;
- um claim em `section_claim_map[section_id]` pertence à seção da chave;
- um claim mapeado a uma pergunta pertence à seção da pergunta;
- `allowed_claim_ids` e `prohibited_claim_ids` existem no grafo e são mutuamente exclusivos;
- um conflito contém somente claims da seção declarada.

### Prova executável

Um estado em que a seção `analysis` apontava para `q_foundation_central_1` foi aceito pelo Pydantic sem erro.

**Correção:** fechar todas as relações, não apenas a existência dos nós.

---

## P1-05 — O detector factual deixa passar frases claramente verificáveis

`backend/app/services/editorial_v3/text_integrity.py:191-212` classifica como factual apenas frases com URL, número ou palavras presentes em uma lista estática de marcadores.

### Prova executável

As quatro frases abaixo foram classificadas como não factuais:

```text
O solo argiloso retém umidade.
A clorofila absorve luz azul e vermelha.
As raízes transportam água para os tecidos.
A condensação forma gotículas na tampa.
```

Todas são afirmações verificáveis e poderiam passar sem evidence ID quando o modelo também as marcar como editoriais.

### Impacto

- fatos sem números podem escapar do fact-check;
- o risco varia por domínio e idioma;
- adicionar verbos manualmente nunca cobrirá todos os predicados factuais.

### Correção

Combinar:

1. regras determinísticas para números, datas, comparações, causalidade e recomendações;
2. classificador semântico independente;
3. política conservadora para sentenças declarativas em seções de pesquisa;
4. whitelist explícita apenas para navegação, transição e opinião editorial.

---

## P1-06 — Regras do conflito não são verificadas integralmente na redação

O grafo produz `required_language` e `prohibited_conclusions` em `content_intelligence.py:463-475`, mas `validate_draft` não percorre essas regras.

A única proteção é a política `context_only`, baseada em uma lista limitada de marcadores de incerteza (`content_intelligence.py:883-894`). Isso não garante que:

- todas as posições relevantes sejam representadas;
- a divergência seja explicada;
- a conclusão proibida não apareça;
- a condição que resolve parcialmente o conflito seja preservada.

**Correção:** criar validação por `conflict_id`, com frases vinculadas, posições representadas e linguagem obrigatória.

---

## P1-07 — Snapshot não está criptograficamente ligado ao rascunho validado

`EditorialIntelligenceRepository.save`, em `backend/app/services/editorial_v3/intelligence_repository.py:39-72`, calcula checksum apenas do `ContentIntelligenceState`.

A tabela `editorial_intelligence_snapshots`, em `backend/app/db/models.py:818-852`, não possui:

- `draft_checksum`;
- `draft_version_id`;
- hash do conjunto de sentenças/evidências;
- referência obrigatória à versão do artigo.

O estado pode dizer `draft_validated`, mas o snapshot não prova qual rascunho foi validado.

**Correção:** toda validação de draft deve persistir `validated_artifact_hash`, `article_version_id` e `draft_revision`.

---

## P1-08 — Revisores preservam o conjunto de claims do bloco, não o vínculo por frase

Em `executor.py:3391-3405`, a proteção compara apenas o conjunto de claim IDs antes e depois do bloco.

Assim, um editor pode mover claim A da frase 1 para a frase 2 e claim B no sentido contrário, mantendo o mesmo conjunto do bloco. A checagem posterior reduz parte do risco, mas a garantia de rastreabilidade por frase foi quebrada temporariamente e pode ser mascarada quando frases combinam conteúdo semelhante.

**Correção:** comparar uma assinatura por sentença:

```text
normalized_sentence + ordered_claim_ids + factuality + conditions
```

Alterações devem gerar nova validação, nunca ser tratadas como preservação automática.

---

## P1-09 — Colisão no fact-check de frases duplicadas

Em `executor.py:4272-4316`, a chave do check é:

```python
(block_id, normalized_text(sentence.text))
```

Duas frases textualmente idênticas dentro do mesmo bloco colidem no dicionário `expected`. Uma pode sobrescrever a outra, inclusive quando possuem evidências diferentes.

**Correção:** cada sentença precisa de `sentence_id` estável. O fact-check deve referenciar esse ID, não o texto como chave primária.

---

## P1-10 — Claims combinados podem criar suporte “Frankenstein”

Em `executor.py:4198-4223`, quando nenhum claim individual atinge o limiar, o sistema concatena os textos de todos os claims e aceita a frase se o texto combinado atingir `0.38`.

Isso pode validar uma relação, causalidade ou conclusão que não existe em nenhuma fonte individual, apenas porque partes das palavras aparecem em claims diferentes.

**Correção:** uma frase composta deve ser decomposta em proposições atômicas. Cada proposição precisa de um claim individual ou de uma relação explícita aprovada no Evidence Graph.

---

## P1-11 — O payload do Writer pode exceder o limite e falhar sem degradação controlada

O limite rígido é `400.000` caracteres em `backend/app/core/config.py:78-81`, e `_task_data_prompt` gera erro acima desse valor em `backend/app/services/agent_runtime.py:84-96`.

O input do Writer, em `executor.py:1648-1732`, duplica ou sobrepõe grandes estruturas:

- contrato;
- dossiers;
- claim catalog;
- referências;
- briefing;
- estado de inteligência;
- sequência editorial;
- matrizes e métodos.

Não existe seleção por seção, compactação, paginação ou orçamento de tokens antes da chamada.

**Correção:** criar `ContextBudgetPlanner`, medir tokens por componente e enviar ao Writer somente o subgrafo necessário para a seção/etapa.

---

## P1-12 — O estado fica semanticamente desatualizado durante revisões

Após o Writer, o estado é marcado como `draft_validated` em `executor.py:1904-1918`. O Development Editor e outros estágios podem alterar o draft, mas o lifecycle não volta para `draft_pending_validation` ou equivalente.

Ele só é novamente marcado após a edição de linguagem (`executor.py:2117-2130`). Em caso de falha, retry ou observabilidade intermediária, o estado pode afirmar que o rascunho está validado quando o draft atual já é outro.

**Correção:** invalidar a aprovação a cada mutação e usar lifecycle ligado à revisão do artefato.

---

# 5. Falhas médias e lacunas de qualidade — P2

## P2-01 — O “planejador de inteligência” ainda é predominantemente determinístico

As perguntas são derivadas diretamente dos campos do contrato. Isso é bom como piso, mas não identifica automaticamente:

- ambiguidades do tema;
- entidades ausentes;
- pré-requisitos implícitos;
- objeções do leitor;
- decisões que variam por contexto;
- perguntas emergentes das fontes;
- contradições entre intenção comercial e evidência.

A próxima evolução deve ampliar o mapa semanticamente, mantendo validação determinística e limites de custo.

## P2-02 — Métrica lexical de alinhamento é vulnerável a palavras genéricas

A prova de temperatura versus pigmentação ainda obteve scores entre `0.1429` e `0.25` por termos comuns. O limiar de `0.08` não é um critério confiável de resposta.

## P2-03 — A suíte atual testa presença, não correção semântica

O teste `test_evidence_graph_closes_provenance_and_writer_gate_passes` exige apenas que o `question_claim_map` não esteja vazio. Não exige relevância.

O teste de corroboração reutiliza o mesmo claim ID, omitindo a forma como o executor cria IDs diferentes por fonte.

## P2-04 — A validação frontend não foi reproduzível a partir do ZIP

O pacote não inclui dependências instaladas, o que é normal para distribuição, mas esta auditoria não pôde reproduzir os 67 testes declarados sem executar `npm ci` com acesso ao registry. O comando retornou `vitest: not found`.

## P2-05 — Ainda falta um E2E real do pipeline completo

Mocks e `model_construct` não validam:

- serialização real no PostgreSQL;
- concorrência e retries do Celery;
- resume após crash;
- latência e limite dos modelos;
- respostas JSON truncadas;
- quotas e falhas Tavily/Serper;
- comportamento do frontend com eventos fora de ordem.

---

# 6. Como o fluxo correto deve funcionar

```text
1. Briefing validado
   ↓
2. Contrato de conhecimento
   ↓
3. Mapa semântico de perguntas e critérios de resposta
   ↓
4. Plano de pesquisa com slots reservados por pergunta crítica
   ↓
5. Descoberta e leitura de fontes
   ↓
6. Claims de fonte persistidos
   ↓
7. Canonicalização por support_group validado
   ↓
8. Evidence Graph com corroboração, contradição e proveniência
   ↓
9. Gate de inteligência
   ├─ passou → plano de escrita
   └─ falha recuperável → pesquisa direcionada → reconstrução do grafo
   ↓
10. Writer recebe question_evidence_plan por seção
   ↓
11. Cada frase factual possui sentence_id + claim_ids
   ↓
12. Answer Map liga pergunta → frases → claims → fontes
   ↓
13. Validação independente de cobertura e entailment
   ↓
14. Revisores alteram uma nova revisão e invalidam aprovação anterior
   ↓
15. Novo fact-check por sentence_id
   ↓
16. Quality Gate valida o hash exato do draft final
   ↓
17. Promoção para conteúdo final
```

---

# 7. Plano de correção recomendado — V3.6.1

## Fase A — Corrigir o modelo de dados

1. Adicionar `canonical_claim_id` aos registros e edges do grafo.
2. Adicionar `sentence_id` a toda sentença do draft.
3. Adicionar `question_answer_map` e status de resposta.
4. Adicionar `validated_artifact_hash` e `article_version_id` aos snapshots.
5. Adicionar estado `draft_pending_validation` ou revisão equivalente.

## Fase B — Corrigir cobertura e pesquisa

1. Remover fallback de pergunta para todos os claims da seção.
2. Exigir alinhamento semântico e claim autorizado.
3. Reservar slots de query para perguntas críticas.
4. Criar recovery tasks a partir dos blockers do Intelligence Gate.
5. Reexecutar síntese e grafo incrementalmente após recuperação.

## Fase C — Corrigir conflitos e políticas

1. Levar claims disputados ao grafo, mesmo sem aprovação para escrita direta.
2. Aplicar `required_language` e `prohibited_conclusions` por conflito.
3. Aplicar regras específicas de cada seção.
4. Distinguir ausência de evidência, divergência e evidência contraditória.

## Fase D — Corrigir Writer e revisores

1. Enviar `question_evidence_plan` ao Writer.
2. Exigir `question_ids` e `sentence_id` no output.
3. Validar resposta por pergunta, não só uma frase factual por seção.
4. Preservar evidence binding por sentença nas revisões.
5. Remover aprovação por concatenação arbitrária de claims.

## Fase E — Robustez operacional

1. Implementar orçamento de contexto por componente.
2. Compactar o subgrafo por seção.
3. Testar crash/resume em cada transição.
4. Testar PostgreSQL, Redis, Celery e provedores em staging.
5. Medir custo, latência, recuperação e taxa de bloqueio falso.

---

# 8. Testes obrigatórios antes de liberar a V3.6.1

1. Pergunta de temperatura + claim sobre pigmentação deve bloquear.
2. Claim correto, porém não autorizado no dossier, não deve cobrir pergunta.
3. Draft sem resposta à pergunta central deve bloquear.
4. Resposta parcial deve ser marcada como parcial.
5. Duas fontes com mesmo `support_group` e IDs distintos devem formar um claim canônico.
6. Claims disputados devem aparecer no grafo, mas não como escrita direta.
7. Intelligence Gate deve criar recovery task para lacuna recuperável.
8. Seis queries originais não podem eliminar todas as queries canônicas.
9. Proibição específica de seção deve bloquear apenas naquela seção.
10. Relação de pergunta cruzada entre seções deve falhar no schema.
11. Frases factuais sem número ou marcador fixo devem exigir classificação semântica.
12. Duas frases iguais no mesmo bloco devem possuir checks independentes por `sentence_id`.
13. Trocar claims entre frases durante revisão deve bloquear.
14. Snapshot deve falhar se o hash do draft não corresponder.
15. Payload acima do orçamento deve ser compactado, não apenas abortado.
16. Um crash após cada estágio deve retomar exatamente a revisão correta.
17. E2E com uma fonte corroboradora, uma contraditória e uma comercial.
18. E2E em `pt-BR`, `en-US` e `es-ES`.
19. E2E com conteúdo curto e longo sem ultrapassar contexto.
20. E2E com respostas JSON inválidas/truncadas dos provedores.

---

# 9. Critério de conclusão

A correção só deve ser considerada pronta quando:

- nenhuma pergunta crítica puder passar com claim irrelevante;
- toda resposta final estiver ligada a pergunta, frase, claim e fonte;
- corroboração e conflito forem preservados no grafo;
- o motor tentar recuperação antes de bloquear lacunas recuperáveis;
- revisões invalidarem e revalidarem a versão exata do draft;
- snapshots identificarem criptograficamente o artefato validado;
- testes de regressão reproduzirem todos os bugs desta auditoria;
- staging completar o fluxo com infraestrutura e provedores reais.

---

## 10. Prioridade final

A ordem recomendada é:

```text
1. canonicalização de claims
2. cobertura pergunta→claim→frase
3. recuperação orientada pelo Intelligence Gate
4. conflitos e regras por seção
5. sentence_id e revisão segura
6. snapshot ligado ao draft
7. orçamento de contexto
8. E2E real
```

Não é recomendável adicionar novos agentes antes dessas correções. O sistema já possui agentes suficientes; o gargalo atual é **integridade das relações e validação do fluxo**, não quantidade de chamadas de IA.

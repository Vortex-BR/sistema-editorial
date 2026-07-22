# Análise robusta do sistema de geração de conteúdo — Editorial V3.5

**Pacote auditado:** `docker-seo-v3.5-atualizado.zip`  
**SHA-256:** `250419723666154cbde6cd04f10851df9ddb45e5188f3856c79b161bf28bcbef`  
**Data da análise:** 20/07/2026  
**Escopo:** contrato editorial, síntese, redação, revisão, fact-checking, edição de linguagem, renderização, SEO, persistência e gate de qualidade. A pesquisa foi examinada somente quando sua implementação afeta diretamente a fidelidade do conteúdo.

---

## 1. Veredito executivo

A versão V3.5 **não deve ser considerada segura para geração de conteúdo em produção no estado atual**.

Existe um defeito bloqueador no caminho entre os agentes V3 e os provedores de IA: os dados reais de cada tarefa são gravados no banco como `input_json`, mas **não são incluídos no prompt enviado ao modelo**. Isso afeta o arquiteto, extrator de claims, sintetizador, redator, reparador e todos os revisores.

Na prática, o redator recebe instruções como “escreva a partir do contrato validado”, mas não recebe o contrato, os dossiês, os claims, o briefing ou o rascunho a revisar. O mesmo ocorre com o fact-checker: ele recebe a ordem para comparar frases com claims, mas não recebe o rascunho nem os claims no pedido enviado ao provedor.

Além desse bloqueio, a auditoria encontrou falhas capazes de:

- permitir fatos sem sustentação real;
- aprovar citações que não existem de forma contínua na fonte;
- ignorar briefing, SEO, público, marca, oferta e restrições informadas pelo usuário;
- alterar significado depois do fact-check;
- eliminar encerramentos e CTAs do contrato;
- forçar seções condicionais irrelevantes;
- gerar conteúdo em português mesmo quando o projeto estiver em outro idioma;
- destruir tabelas e callouts na finalização;
- ignorar limites de palavras, H2 e H3;
- persistir conteúdo como “final” antes de o gate final aprová-lo.

**Prioridade obrigatória:** corrigir os itens `GEN-001`, `GEN-002` e `GEN-003` antes de qualquer novo teste editorial em produção.

---

## 2. Matriz de risco

| ID | Severidade | Falha | Efeito principal |
|---|---:|---|---|
| GEN-001 | **P0 — bloqueador** | `input_json` não chega ao modelo | Agentes trabalham sem contrato, claims, documentos ou rascunho |
| GEN-002 | **P0 — crítico** | Integridade factual depende de declarações do próprio modelo | Fatos podem passar sem evidência ou com evidência irrelevante |
| GEN-003 | **P0 — crítico** | Verificação de citação e agrupamento de claims são frágeis | Fontes podem “confirmar” afirmações diferentes ou citações inexistentes |
| GEN-004 | **P1 — alto** | Writer não recebe catálogo completo de claims | Mapeamento frase → evidência fica incompleto |
| GEN-005 | **P1 — alto** | Briefing, SEO, audiência, marca e oferta são amplamente ignorados | Conteúdo genérico, fora do objetivo e sem posicionamento correto |
| GEN-006 | **P1 — alto** | Idioma fixado em português brasileiro | Projetos multilíngues produzem idioma incorreto |
| GEN-007 | **P1 — alto** | Nós de fechamento não pesquisáveis são removidos | Texto termina sem conclusão, CTA ou ponte comercial |
| GEN-008 | **P1 — alto** | Nós opcionais/condicionais viram obrigatórios | Filler, seções artificiais e bloqueios falsos |
| GEN-009 | **P1 — alto** | Revisões locais podem mudar fatos após o fact-check | Conteúdo final pode divergir da evidência aprovada |
| GEN-010 | **P1 — alto** | Contratos de revisão aceitam “passed” inconsistente | Fact-check vazio ou com achados graves pode passar |
| GEN-011 | **P1 — alto** | Todos os revisores usam a mesma rota `editor` | Erros correlacionados e ausência de independência real |
| GEN-012 | **P1 — alto** | Tabelas, callouts e citações visíveis são perdidos | Estrutura e rastreabilidade editorial são degradadas |
| GEN-013 | **P1 — alto** | Cálculo da faixa de palavras ignora máximo do briefing | Artigos podem exceder muito o limite solicitado |
| GEN-014 | **P1 — alto** | Regras de H2/H3, seções obrigatórias e claims proibidos não são aplicadas | Entrega viola o briefing aceito pela API |
| GEN-015 | **P1 — alto** | SEO final é mecânico e pode alterar sentido | Slug quebrado, título cortado e meta enganosa/incompleta |
| GEN-016 | **P1 — alto** | Título/H1 podem conter afirmações factuais sem evidência | Claim forte pode escapar do fact-check |
| GEN-017 | **P1 — alto** | Arquitetura procedural exige dois métodos e links externos | Guias de método único são artificialmente distorcidos |
| GEN-018 | **P1 — alto** | Conteúdo de fontes é tratado como dado confiável para o agente | Risco de prompt injection vindo de páginas pesquisadas |
| GEN-019 | **P2 — médio** | Documentos são truncados e selecionados por ordem, não por relevância | Evidência importante no fim da fonte pode desaparecer |
| GEN-020 | **P2 — médio** | Conteúdo é persistido em campos finais antes da aprovação | Conteúdo bloqueado pode ser consumido por integração descuidada |
| GEN-021 | **P2 — médio** | Envelope de saída varia e pode ser insuficiente | JSON truncado e geração incompleta conforme provedor/rota |
| GEN-022 | **P2 — médio** | Ausência de verificação de similaridade e canibalização na V3 | Conteúdo pode repetir páginas existentes |
| GEN-023 | **P2 — médio** | Idempotência não compara hash da entrada | Resultado antigo pode ser reutilizado após mudança de estado |
| GEN-024 | **P2 — médio** | Prompts e skills contêm regras fixas e erros gramaticais | Tendência a prosa artificial e instruções contraditórias |

---

# 3. Falhas detalhadas

## GEN-001 — Os dados da tarefa não são enviados ao modelo

### Evidência no código

Em `backend/app/services/agent_runtime.py`:

- linhas **196–206**: o contexto é composto somente com `prompt`;
- linhas **207–221**: `input_json` é sanitizado e armazenado em `traced_input`;
- linhas **396–401**: o gateway recebe somente `composed.prompt`.

Em `backend/app/services/agent_context.py`, linhas **148–159**, o texto compilado contém contexto superior, memórias, handoff e `<task>`, mas nenhum bloco com os dados da tarefa.

Em `backend/app/services/llm_gateway.py`:

- OpenAI, linhas **642–650**: envia apenas `prompt`;
- Anthropic, linhas **754–758**: envia apenas `prompt`;
- Gemini, linhas **839–845**: envia apenas `prompt`.

Todos os estágios V3 colocam os dados essenciais em `input_json`:

- taxonomia de abordagens: `executor.py:377`;
- inventário de métodos: `executor.py:1146`;
- síntese do conhecimento: `executor.py:1211` e `1285`;
- writer: `executor.py:1501`;
- reparo do writer: `executor.py:1519`;
- extração de claims: `executor.py:2058`;
- revisões: `executor.py:2477`, `2519` e `2588`.

### Por que os testes não detectaram

`backend/tests/test_superior_context_enforcement.py`, linhas **175–204**, afirma explicitamente que `private_input` **não deve chegar ao prompt**. Essa regra é correta para metadados privados, mas a V3 usa o mesmo `input_json` para carregar o conteúdo público indispensável à tarefa. Não existe um segundo canal para `task_data`.

Os testes dos estágios V3 frequentemente substituem `_agent_call` por `AsyncMock`, validando apenas a resposta simulada, sem capturar o request real enviado a OpenAI, Anthropic ou Gemini.

### Impacto

- claim extraction sem documentos;
- synthesis sem claims;
- writer sem contrato e dossiês;
- fact-checker sem rascunho e evidências;
- revisores sem blocos para revisar;
- custos estimados sem considerar o payload real que deveria ser enviado.

### Correção obrigatória

Separar os conceitos:

```python
runtime.call(
    ...,
    trace_input=private_or_audit_metadata,
    task_data=public_generation_payload,
    prompt=task_instructions,
)
```

O `task_data` deve ser serializado em um bloco delimitado e tratado como dados não confiáveis:

```text
<task_instructions>...</task_instructions>
<untrusted_task_data format="json">...</untrusted_task_data>
```

A correção também deve:

1. incluir `task_data` no cálculo de tokens e custo;
2. incluir hash da entrada na idempotência;
3. impor orçamento de contexto por estágio;
4. impedir que segredos sejam colocados em `task_data`;
5. testar o payload real dos três provedores com um valor sentinela.

**Não basta concatenar indiscriminadamente todo o `input_json` ao prompt**, pois alguns payloads são muito grandes e documentos externos podem conter instruções maliciosas.

---

## GEN-002 — O sistema pode aprovar fatos sem sustentação real

### 2.1 O próprio modelo decide se uma frase é factual

`backend/app/schemas/editorial_v3_runtime.py`, linhas **357–368**:

- `is_factual` é informado pelo modelo;
- evidência só é exigida quando o próprio modelo marca `true`;
- uma afirmação factual marcada como `false` passa sem evidência.

`executor.py`, linhas **3002–3032**, apenas verifica se os IDs citados existem. Não existe classificador determinístico capaz de detectar números, causalidade, comparações, promessas, condições técnicas ou afirmações verificáveis marcadas como “editoriais”.

### 2.2 O `entailment_score` é autodeclarado e não é validado

`V3EvidenceReference.entailment_score`, linhas **352–355**, aceita qualquer número entre 0 e 1. A busca no backend mostra que, na V3, esse score é armazenado, mas não é recalculado nem comparado com um limite de aprovação.

### 2.3 O fact-check pode passar sem verificar todas as frases

`V3FactCheckReview`, linhas **451–456**, não possui validator que exija:

- uma checagem para cada frase factual;
- correspondência exata entre `sentence_text`, `block_id` e o rascunho;
- falha quando existe check `unsupported` ou `contradicted`;
- falha quando há finding crítico;
- lista de checks não vazia.

Logo, uma resposta como esta é estruturalmente válida:

```json
{"status":"passed","checks":[],"findings":[],"rewrite_block_ids":[]}
```

### 2.4 O gate de qualidade confia nessa autoavaliação

`universal_quality.py`, linhas **152–154**, dá integridade factual máxima se o status do fact-check for `passed` e as frases marcadas como factuais tiverem alguma evidência. Ele não testa se a evidência implica a frase.

### Correção

Implementar quatro verificadores independentes:

1. **classificador factual determinístico/híbrido** para título, headings e sentenças;
2. **verificador de cobertura**, exigindo um `ClaimCheck` para cada frase factual;
3. **verificador de entailment** entre frase e `claim_text`, preservando negação, números, condições e grau de certeza;
4. **validator de consistência do review**, impedindo `passed` quando houver checks não suportados, findings graves ou lacunas.

---

## GEN-003 — Aprovação de claims pode usar citações falsas ou claims diferentes

### 3.1 O verificador de citação aceita palavras embaralhadas

`backend/app/services/editorial_v3/artifact_repository.py`, linhas **78–85**:

```python
if normalized_quote in normalized_text:
    return True
...
return ... 90% dos tokens existem no texto
```

O fallback usa um conjunto de tokens e ignora ordem, proximidade e continuidade. Um teste direto da implementação confirmou que uma citação de dez palavras foi aprovada mesmo com as palavras espalhadas e embaralhadas no documento.

Isso permite que `quote_verified=True` seja salvo nas linhas **232–275** sem existir um trecho contínuo correspondente.

### 3.2 O modelo controla o `support_group`

O prompt de extração, `executor.py:2073–2079`, pede ao modelo para reunir afirmações semanticamente equivalentes no mesmo `support_group`.

`approve_claim_bundles`, `artifact_repository.py:302–357`, agrupa e aprova com base nesse identificador, diversidade de hosts e política de fonte. Não há uma verificação independente de equivalência semântica, compatibilidade de unidades, condições, população, negação ou conflito entre os textos dos claims.

Duas afirmações diferentes — ou contraditórias — podem ser tratadas como corroboradas se o modelo lhes der o mesmo grupo.

### 3.3 A normalização de grupos remove caracteres acentuados em vez de transliterar

`executor.py:121–123` transforma qualquer caractere fora de `[a-z0-9]` em `_`. O mesmo helper é usado para `support_group` em `executor.py:2125–2127`.

Exemplos:

```text
ação -> a_o
aço  -> a_o
órgão -> rg_o
```

Isso pode criar colisões entre conceitos distintos em português e agrupar claims indevidamente.

### Correção

- validar citação por substring normalizada contínua;
- permitir fuzzy matching apenas em janelas locais e com ordem preservada;
- rejeitar match global por conjunto de tokens;
- gerar o grupo de suporte deterministicamente ou confirmar equivalência com NLI/embedding e regras numéricas;
- detectar contradições antes de aprovar o bundle;
- usar transliteração Unicode estável e hash semântico para IDs técnicos.

---

## GEN-004 — O writer não recebe o catálogo completo de claims

Em `executor.py:1413–1444`, `writer_input` contém dossiês e apenas `approved_claim_ids`. Não contém um catálogo compacto com:

- `claim_id`;
- `claim_text`;
- condições;
- limitações;
- status da conclusão;
- papel da evidência;
- origem.

Mesmo depois de corrigir o GEN-001, o redator dependerá de resumos produzidos pelo sintetizador e terá dificuldade para associar uma frase específica ao claim correto.

### Correção

Adicionar `claim_catalog` ao payload do writer e dos revisores. Para controlar tokens, enviar somente claims autorizados por cada seção, com texto, condições e limitações, e não todas as fontes brutas.

---

## GEN-005 — O briefing aceito pela API não governa a geração

`ContentBriefWrite`, em `backend/app/schemas/api.py:62–124`, expõe campos importantes. Entretanto, na V3 não há aplicação efetiva de vários deles:

- `commercial_objective`;
- `offer`;
- `desired_action`;
- `segment`;
- idade, estágio de vida e nível de conhecimento do leitor;
- `minimum_h2` e `minimum_h3`;
- `required_sections` do briefing;
- `preferred_sources` e `prohibited_sources`;
- `maximum_source_age_days`;
- `claims_to_avoid`;
- `related_page_url`;
- `approved_style_examples`.

`primary_keyword` e `secondary_keywords` são usados principalmente na formação do assunto de pesquisa (`knowledge_contract.py:150–179`), mas não existe contrato SEO para título, introdução, headings, meta description ou distribuição natural.

`additional_context` é lido, mas o contrato guarda apenas `additional_context_present: bool` (`knowledge_contract.py:875`), descartando o conteúdo no contrato gerado.

O `voice_override` é o único campo de voz colocado no `writer_input` (`executor.py:1423–1425`) — e, pelo GEN-001, também não chega ao modelo.

### Perfil de publicação

O manifesto contém snapshot completo do perfil em `execution_manifest.py:555–576`, incluindo marca, proposta de valor, audiência e tom. O composer e o executor V3 não o incorporam ao contexto de geração.

### Impacto

- conteúdo genérico;
- CTA ausente ou incompatível;
- oferta incorreta;
- voz de marca perdida;
- termos/claims proibidos usados;
- nível técnico inadequado ao leitor;
- requisitos SEO aceitos pela interface, mas não cumpridos.

### Correção

Criar um `GenerationBrief` versionado e imutável, com campos públicos e validados, utilizado pelo writer, editores, SEO finalizer e quality gate. Cada campo aceito na API deve ter uma destas classificações:

- aplicado deterministicamente;
- enviado ao agente;
- usado somente para pesquisa;
- explicitamente não suportado e rejeitado pela API.

Nenhum campo deve ser silenciosamente ignorado.

---

## GEN-006 — O sistema não é realmente multilíngue

Apesar de o contrato registrar `project_locale`, o writer usa a instrução fixa em `executor.py:1470–1472`:

> “Escreva conteúdo editorial premium em português do Brasil...”

As skills também fixam português brasileiro, por exemplo:

- `skills/default/writing.tone-and-style.yaml`;
- `skills/default/research.fact-extraction.yaml`;
- `skills/default/editorial.language-quality.yaml`;
- `skills/superior/writer.yaml`;
- `skills/superior/editor.yaml`.

O finalizador publica `language=self.project.language` (`executor.py:1825`), podendo declarar inglês ou espanhol enquanto o corpo está em português.

### Correção

- selecionar pacote de idioma por locale;
- remover idioma fixo das skills universais;
- validar o idioma real do texto antes da finalização;
- usar regras de gramática, voz e SEO específicas do locale;
- bloquear quando locale não tiver suporte implementado.

---

## GEN-007 — O nó de fechamento/CTA é apagado antes da redação

A hierarquia universal cria um nó `closing` não pesquisável para explicação, comparação, troubleshooting e educação comercial (`editorial_hierarchy.py`, incluindo linhas **140–179**).

Porém, `_generic_contract`, em `knowledge_contract.py:890–892`, mantém apenas nós com `research_required=True`.

O nó removido fica apenas em metadata (`knowledge_contract.py:995–1000`) e o writer percorre somente `contract.nodes`.

### Impacto

- conclusão ausente ou improvisada;
- CTA não conectado ao conteúdo;
- educação comercial perde a ponte explícita para a oferta;
- texto pode terminar abruptamente.

### Correção

Manter todos os nós editoriais no contrato de geração. Separar:

```text
research_required
synthesis_required
generation_required
quality_required
```

Um nó pode não exigir pesquisa e ainda ser obrigatório na redação.

---

## GEN-008 — Nós condicionais e opcionais são tratados como obrigatórios

A hierarquia contém nós com aplicabilidade `conditional` ou `optional`. Entretanto:

- o contrato genérico os inclui quando exigem pesquisa;
- o prompt do writer exige todos os nós (`executor.py:1483–1484`);
- `_draft_diagnostics` considera todos obrigatórios (`executor.py:2712–2732`);
- os serviços de qualidade fazem a mesma comparação.

### Impacto

O sistema pode inventar mitos, objeções, prevenção, preparação ou exceções apenas para preencher o molde. Também pode bloquear um artigo correto porque uma seção condicional não era aplicável.

### Correção

Antes da pesquisa, resolver a aplicabilidade de cada nó em:

```text
required
included
omitted_with_reason
```

O gate deve exigir apenas os nós ativos. A omissão precisa ser auditável e não decidida silenciosamente pelo writer.

---

## GEN-009 — Revisões podem alterar fatos depois do fact-check

`_review_and_revise`, em `executor.py:2518–2584`, protege somente:

- `block_id`;
- posição;
- `section_id`;
- `method_id`;
- tipo do bloco.

O próprio modelo informa `meaning_changed`. O código confia nesse booleano e não compara semanticamente o bloco original com o revisado.

Não há verificação de que:

- todos os blocos pedidos foram devolvidos;
- números e unidades permaneceram iguais;
- negações e condicionais foram preservadas;
- o conjunto de evidências de cada proposição permaneceu equivalente;
- nenhuma nova afirmação factual foi criada;
- frases factuais não foram remarcadas como não factuais.

A edição de linguagem ocorre **depois** do fact-check (`executor.py:1671–1714`). Depois dela, o sistema apenas confirma que os IDs de evidência existem, sem executar um novo fact-check.

### Correção

- exigir exatamente o conjunto de blocos solicitado;
- comparar original e revisão com entailment bidirecional;
- bloquear novos números, entidades, causalidade ou negação;
- preservar/revalidar a classificação factual;
- reexecutar o fact-check nos blocos modificados pelo language editor;
- aplicar `_validate_draft_evidence` e diagnósticos completos após toda revisão.

---

## GEN-010 — Contratos de revisão aceitam estados contraditórios

### Fact-check

`V3FactCheckReview` não possui validator. Pode retornar `status="passed"` com:

- checks `unsupported`;
- checks `contradicted`;
- findings críticos;
- zero checks;
- `rewrite_block_ids` preenchidos.

### Language review

`V3LanguageReview` também não vincula status, findings e blocos para reescrita.

### Development review

O validator bloqueia apenas finding crítico ou pesquisa faltante. Achados `major` podem coexistir com `passed`.

### Correção

Adicionar invariantes estruturais:

- `passed` exige zero findings major/critical e zero blocos para rewrite;
- `rewrite` exige blocos válidos e findings corrigíveis;
- `blocked` exige motivo crítico ou pesquisa ausente;
- fact-check `passed` exige uma checagem suportada por frase factual;
- checks devem apontar para texto e bloco existentes no draft.

---

## GEN-011 — Revisores não são independentes

Development editor, fact-checker, language editor e suas revisões chamam `role="editor"` em `executor.py:2477`, `2519` e `2588`.

As skills de estágio variam, mas a rota, o modelo, a persona superior e as memórias continuam sendo do mesmo papel `editor`.

### Impacto

- o mesmo modelo tende a confirmar a própria interpretação;
- erros sistemáticos são replicados;
- o “fact-check independente” não é realmente independente;
- scores de qualidade são autorrelatados pela mesma família de julgamento.

### Correção

Criar papéis e rotas próprias:

```text
development_editor
fact_checker
language_editor
```

Para claims críticos, permitir provedor/modelo distinto entre writer e fact-checker.

---

## GEN-012 — Tabelas, callouts e evidências visíveis são destruídos

`V3DraftBlock` aceita `table` e `callout` (`editorial_v3_runtime.py:371–379`), mas não possui estrutura para cabeçalhos, linhas, células ou tipo de callout.

No finalizador (`executor.py:1761–1765`), qualquer tipo fora de H1/H2/H3/parágrafo/lista é convertido para `paragraph` na persistência.

No renderer (`executor.py:1794–1814`), tabela e callout caem no `else` e viram parágrafo.

Além disso, as evidências são mantidas no relatório de rastreabilidade, mas não são renderizadas no Markdown/HTML do artigo. O leitor final não vê citações, notas ou referências por frase.

### Correção

- criar schemas próprios para tabela, callout, nota e referência;
- renderizar HTML/Markdown de forma tipada;
- adicionar referências visíveis configuráveis, sem expor IDs internos;
- incluir seção de fontes ou notas quando o tipo de conteúdo exigir;
- preservar a rastreabilidade interna separadamente.

---

## GEN-013 — Limites de palavras do briefing são ignorados

`executor.py:2650–2680` calcula:

```python
minimum = max(configured_minimum, brief_minimum, scope_minimum)
maximum = max(configured_maximum, brief_maximum, minimum)
```

Com defaults (`core/config.py:57–58`) de 1.800–3.500 palavras, um briefing pedindo 650–900 palavras resulta, na prática, em uma faixa mínima de 1.800 e máxima de 3.500.

O máximo informado pelo usuário nunca reduz o máximo global, pois `max()` escolhe o maior valor.

Além disso, os quality services só bloqueiam excesso acima de **115%** do máximo, permitindo ultrapassar um limite que pode ter sido declarado como obrigatório.

### Correção

Tratar os limites por função:

- limite global máximo = teto de segurança;
- máximo do briefing = limite editorial;
- mínimo estrutural = requisito calculado;
- se mínimo estrutural > máximo solicitado, rejeitar o briefing antes da execução e explicar o conflito.

Exemplo:

```python
maximum = min(configured_maximum, brief_maximum) if brief_maximum else configured_maximum
minimum = max(configured_minimum_for_architecture, brief_minimum, scope_minimum)
if minimum > maximum:
    raise BriefConflict(...)
```

O `configured_minimum` deve ser específico por arquitetura, não um mínimo universal de 1.800 palavras.

---

## GEN-014 — Requisitos editoriais aceitos não são validados

Não há aplicação efetiva de:

- mínimo de H2;
- mínimo de H3;
- seções obrigatórias informadas pelo usuário;
- claims que devem ser evitados;
- fontes preferidas/proibidas;
- idade máxima da fonte;
- link interno relacionado;
- exemplos de estilo aprovados.

O sistema valida apenas os nós internos do contrato, que não são equivalentes às seções requisitadas no briefing.

### Correção

Criar um `BriefComplianceReport` determinístico executado:

1. antes da pesquisa;
2. antes da redação;
3. depois da edição de linguagem;
4. no quality gate.

Cada requisito deve produzir status `satisfied`, `not_applicable`, `blocked` ou `waived_with_reason`.

---

## GEN-015 — Finalização SEO é frágil

### Slug

`_slug`, `executor.py:121–123`, remove acentos em vez de transliterar:

```text
Guia de germinação -> guia-de-germina-o
seleção -> sele-o
```

O mesmo problema também afeta IDs internos de claims e support groups.

### SEO title

`executor.py:1822` usa `draft.title[:60]`, podendo cortar palavra, intenção ou qualificador.

### Meta description

`executor.py:3086–3093` concatena os primeiros parágrafos e corta em 155 caracteres. Não valida:

- keyword/intenção;
- benefício;
- clareza;
- CTA;
- sentença completa;
- preservação de condicionais.

Uma frase pode ser cortada antes de “quando”, “desde que”, “não” ou outra limitação, alterando a mensagem no snippet.

### H1

O finalizador apenas cria um H1 se nenhum existir. Não impede múltiplos H1 nem garante que o H1 seja igual ao título.

### Correção

Implementar `SEOFinalizerService` com:

- transliteração Unicode;
- título word-safe;
- meta description gerada/validada como unidade editorial;
- exatamente um H1;
- H1 alinhado ao title;
- hierarquia sem saltos;
- verificação de intenção e keyword sem densidade artificial;
- validação factual do title e da meta.

---

## GEN-016 — Título e headings podem carregar claims sem evidência

`V3WriterOutput.title` é um texto livre sem campo de evidência. Se o título disser “Método X dobra o resultado”, o sistema não consegue associar esse claim a uma fonte.

O `ClaimCheck` exige `block_id`, mas o título não é um bloco. O renderer pode ainda inserir o título como H1 automaticamente (`executor.py:1815–1817`).

O próprio prompt do writer afirma que “títulos ... são não factuais” (`executor.py:1478–1479`), premissa incorreta: títulos e subtítulos podem conter números, comparações, causalidade e promessas verificáveis.

### Correção

- representar título como bloco rastreável ou criar `title_evidence`;
- classificar factualidade de title/headings;
- impedir claims fortes em metadados sem evidência;
- incluir title, H1 e meta no fact-check final.

---

## GEN-017 — Guias procedurais de método único não são suportados

A API exige comparação, link externo e pelo menos dois métodos para `procedural_decision_guide` (`api.py:213–223`). O mesmo requisito aparece no contrato e nos schemas de runtime:

- `knowledge_contract.py:273–279`;
- `editorial_v3.py:544–549`;
- `MethodInventoryOutput`: mínimo de dois métodos (`editorial_v3_runtime.py:168–170`);
- `KnowledgeSynthesisOutput`: mínimo de dois métodos (`editorial_v3_runtime.py:301–304`).

### Impacto

Um artigo “como executar um único processo” precisa ser classificado como explicativo ou receber comparações artificiais, prejudicando intenção, clareza e profundidade.

### Correção

Adicionar arquitetura separada:

```text
procedural_how_to
```

Ela deve aceitar um método, não exigir matriz comparativa e tornar referência externa dependente da política editorial, não obrigatória em todos os casos.

---

## GEN-018 — Falta proteção contra prompt injection de fontes

A extração de claims envia seções e parágrafos de páginas externas ao agente (`executor.py:2062–2071` e `_document_for_agent`, `3035–3058`). Não há uma camada que marque explicitamente o conteúdo como não confiável nem sanitização semântica de instruções presentes na página.

Depois de corrigir o GEN-001, uma página pode conter texto como “ignore as instruções anteriores” e tentar interferir na extração.

### Correção

- encapsular fontes em bloco de dados não confiáveis;
- instruir o modelo a nunca executar comandos encontrados nos documentos;
- remover scripts, prompts ocultos e texto de navegação;
- detectar padrões de injection;
- usar extração por documento/chunk e validação determinística posterior;
- nunca misturar conteúdo de fonte com instrução de sistema.

---

## GEN-019 — Amostragem e truncamento podem excluir a melhor evidência

`executor.py:2043–2055` seleciona até seis documentos por tarefa usando a ordem atual, sem ordenar explicitamente por relevância, autoridade, cobertura ou diversidade.

`_document_for_agent`, `executor.py:3035–3058`, limita cada documento a:

- 20 seções;
- 12 parágrafos por seção;
- 20 passos;
- 20 itens;
- 4 tabelas.

A evidência relevante pode estar no final de uma norma, paper, manual ou FAQ extensa e nunca ser apresentada ao extrator.

### Correção

- ranquear chunks, não apenas documentos;
- selecionar por tarefa e evidência requerida;
- garantir diversidade de fonte;
- manter offsets/localizadores;
- executar recuperação de chunks quando uma pergunta continuar sem cobertura;
- registrar quais partes foram omitidas.

---

## GEN-020 — Conteúdo é gravado como final antes do gate final

A ordem do grafo é `finalizer → quality_gate` (`graph.py:159–165`).

O finalizer grava `article.final_markdown`, `article.final_html` e SEO metadata (`executor.py:1862–1877`) antes da avaliação de qualidade. Se o gate falhar, apenas o status é alterado para `blocked` (`executor.py:2007–2014`).

### Impacto

Uma API, exportador ou integração que leia `final_markdown` sem checar o status pode publicar conteúdo reprovado.

### Correção

Persistir primeiro em campos/versão de candidato e promover para `final_*` somente após o gate. Como defesa adicional, toda leitura pública deve exigir status permitido.

---

## GEN-021 — O orçamento de saída pode truncar o artigo estruturado

O catálogo define writer com 12.000 tokens (`model_catalog.py:70–74`), a rota padrão não OpenAI usa 8.192 (`api/routes.py:181–187`), enquanto a migration 0031 ajusta algumas rotas existentes para 20.000.

Isso cria comportamento dependente de como a rota foi criada ou salva. Um artigo de 3.500 palavras em JSON estrito, com blocos, sentenças, flags e evidências, pode consumir muito mais tokens do que o texto final.

### Correção

- derivar output budget do alvo de palavras + overhead do schema;
- bloquear preflight quando a rota não suporta o envelope;
- alinhar catálogo, migrations e defaults;
- preferir geração por seções com montagem determinística;
- validar truncamento e `finish_reason` do provedor.

---

## GEN-022 — A V3 não verifica similaridade, duplicação ou canibalização

A busca por mecanismos de similarity/cannibalization no executor e serviços V3 não encontrou uso. O V2 possui contexto de conteúdos semelhantes, mas isso não foi integrado à V3.

Também não há uso efetivo de `related_page_url` para linkagem interna.

### Impacto

- páginas muito parecidas;
- repetição de estrutura e conclusões;
- canibalização de intenção;
- perda de oportunidade de link interno contextual.

### Correção

Adicionar preflight de intenção e pós-geração de similaridade semântica contra conteúdos existentes, com regras para consolidar, diferenciar ou bloquear.

---

## GEN-023 — Reuso idempotente não verifica mudança de entrada

`AgentRuntime.call`, `agent_runtime.py:222–224`, devolve imediatamente `output_json` de um `AgentRun` já concluído.

O ID é derivado de pipeline, papel, chave e tentativa (`executor.py:3178–3182`), mas não inclui hash do `task_data`. Após a correção do GEN-001, uma retomada com entrada materialmente diferente pode reutilizar uma saída antiga.

### Correção

Persistir `input_hash` e comparar antes de reutilizar. Se a entrada mudar, criar nova tentativa ou bloquear inconsistência de resumabilidade.

---

## GEN-024 — Prompts e skills introduzem viés de forma e erros de linguagem

Foram encontrados trechos como:

- “os abordagens”;
- “um abordagem”;
- instruções sempre em português;
- skills procedurais muito presentes em um sistema que pretende ser universal.

Esses erros aparecem em prompts centrais, como `executor.py:1487–1493`, `1152–1155`, `1224–1229` e `1703–1711`.

### Impacto

- modelo imita construções incorretas;
- prosa perde naturalidade;
- regras procedurais podem contaminar conteúdos não procedurais;
- manutenção fica mais difícil por haver instruções repetidas em código e YAML.

### Correção

- revisão linguística de todos os prompts;
- prompt lint automatizado;
- eliminar duplicidade entre código e skills;
- separar regras universais das específicas por arquitetura e idioma;
- teste snapshot dos prompts finais por estágio.

---

# 4. Arquitetura de correção recomendada

## 4.1 Novo envelope de execução dos agentes

```text
System rules
→ Superior/default/stage skills
→ Task instructions
→ Public task data, delimitado e não confiável
→ Output schema
```

Separar:

```text
trace_input: dados de auditoria e possíveis segredos; nunca enviados ao modelo
task_data: dados públicos necessários à tarefa; enviados com orçamento e proteção
```

## 4.2 Novo contrato de geração

Criar `GenerationContext` contendo:

- contrato editorial;
- nós ativos e nós omitidos com motivo;
- briefing completo aplicável;
- perfil de publicação aplicável;
- público e nível técnico;
- SEO intent e keywords;
- claims permitidos por seção;
- claims a evitar;
- CTA/offer policy;
- locale;
- limites estruturais e de tamanho;
- políticas de links e fontes.

## 4.3 Redação por seção

Em vez de um único JSON gigante:

1. gerar título provisório;
2. gerar cada seção com seu dossier e claim subset;
3. validar evidência e tamanho localmente;
4. montar artigo;
5. executar editor de desenvolvimento global;
6. fact-check determinístico + modelo independente;
7. linguagem;
8. novo fact-check dos blocos alterados;
9. SEO finalizer;
10. gate final;
11. promoção para campos finais.

## 4.4 Camada de integridade factual

Cada proposição factual deve ter:

```text
sentence_id
factuality_class
claim_ids
verified_entailment
conditions_preserved
limitations_preserved
fact_check_status
```

O modelo pode sugerir, mas não pode ser a única autoridade para aprovar essas propriedades.

---

# 5. Plano de implementação por prioridade

## Fase 0 — Bloqueio de produção e correção do transporte de dados

1. Desativar execução V3 em produção enquanto `GEN-001` existir.
2. Implementar `task_data` separado de `trace_input`.
3. Incluir token budgeting, injection boundary e hash de entrada.
4. Criar testes de request real para OpenAI, Anthropic e Gemini.
5. Confirmar que writer, fact-checker e claim extractor recebem sentinelas do payload.

## Fase 1 — Integridade factual

1. Corrigir verificação de `exact_quote`.
2. Validar equivalência de support groups.
3. Adicionar factuality classifier.
4. Adicionar entailment verificado.
5. Fortalecer schemas de review.
6. Reexecutar fact-check após linguagem.
7. Incluir title/H1/meta no escopo factual.

## Fase 2 — Briefing, marca e SEO

1. Implementar `GenerationBrief`.
2. Usar perfil de publicação.
3. Aplicar público, objetivo, oferta, CTA e claims proibidos.
4. Aplicar H2/H3, seções requeridas e links internos.
5. Corrigir word range.
6. Implementar SEO finalizer.

## Fase 3 — Arquitetura editorial

1. Preservar closing e outros nós não pesquisáveis.
2. Resolver aplicabilidade de nós condicionais.
3. Criar `procedural_how_to` de método único.
4. Tornar referência externa uma política contextual.
5. Separar geração por arquitetura e idioma.

## Fase 4 — Renderização, revisão e publicação

1. Schemas e renderers de tabela/callout.
2. Referências visíveis configuráveis.
3. Rotas independentes de revisão.
4. Similarity/cannibalization gate.
5. Persistência em candidato e promoção somente após aprovação.

---

# 6. Testes de aceitação obrigatórios

## Transporte de dados

1. Um sentinela em `task_data` deve aparecer no request real dos três provedores.
2. Um sentinela em `trace_input` não pode aparecer no request.
3. O custo estimado deve incluir o `task_data`.
4. Alterar `task_data` deve alterar `input_hash` e impedir reuso incorreto.

## Integridade factual

5. Frase factual marcada como `is_factual=false` deve ser detectada.
6. Evidência com claim não relacionado deve falhar entailment.
7. Fact-check `passed` com checks vazios deve ser inválido.
8. Fact-check `passed` com check `unsupported` deve ser inválido.
9. Citação com palavras embaralhadas deve ser rejeitada.
10. Claims contraditórios no mesmo support group devem ser separados/bloqueados.
11. Título factual sem evidência deve falhar.
12. Revisão de linguagem que altera número, negação ou condição deve reabrir fact-check.

## Briefing

13. `claims_to_avoid` deve bloquear ocorrência sem waiver.
14. `minimum_h2`/`minimum_h3` devem ser cumpridos.
15. `required_sections` deve ser mapeado e validado.
16. Oferta e CTA devem aparecer somente quando solicitados e no nó correto.
17. `approved_style_examples` devem afetar a avaliação de voz sem serem copiados.
18. Projeto `es-ES` deve produzir espanhol e metadata consistente.

## Estrutura e renderização

19. Nó opcional não aplicável deve ser omitido com motivo e não bloquear.
20. Closing comercial deve permanecer no contrato de geração.
21. Guia de método único deve funcionar sem inventar segunda abordagem.
22. Tabela deve sobreviver até Markdown, HTML e persistência.
23. Callout deve sobreviver até Markdown, HTML e persistência.
24. Deve existir exatamente um H1.

## Tamanho e SEO

25. Briefing 650–900 deve produzir faixa compatível ou erro de conflito explícito — nunca 1.800–3.500 silenciosamente.
26. Slug com “germinação” deve resultar em `germinacao`, não `germina-o`.
27. SEO title não pode cortar palavra ou qualificador.
28. Meta description deve ser sentença coerente e passar verificação factual.

## Publicação

29. Conteúdo reprovado não pode estar disponível em campos finais públicos.
30. Conteúdo similar acima do limite deve gerar warning/block conforme política.
31. Documento longo com evidência no fim deve ter seu chunk recuperado.
32. Um artigo no limite máximo deve caber no envelope do modelo sem truncar JSON.

---

# 7. Definition of Done

A correção só deve ser considerada concluída quando:

- nenhum estágio V3 depender de dados que não chegam ao provedor;
- todo campo aceito pelo briefing tiver comportamento documentado e testado;
- toda frase factual, inclusive title/H1/meta, tiver cobertura verificável;
- fact-check não puder passar vazio ou contraditório;
- revisões posteriores não puderem invalidar a evidência silenciosamente;
- idioma, arquitetura e tamanho respeitarem o projeto;
- tabelas, links, callouts e citações sobreviverem à renderização;
- conteúdo só for promovido a final após o gate;
- houver E2E em staging com pelo menos dois provedores, fontes reais e captura do request;
- os testes cobrirem falhas, não apenas respostas simuladas bem-sucedidas.

---

# 8. Validações executadas nesta auditoria

- integridade do ZIP: **aprovada**;
- SHA-256 conferido: **aprovado**;
- `python -m compileall backend/app`: **aprovado**;
- rastreamento estático de todas as chamadas `_agent_call`: **executado**;
- rastreamento do payload até OpenAI/Anthropic/Gemini: **executado**;
- teste direto do verificador de quote com tokens embaralhados: **falha confirmada**;
- teste direto da normalização de slug com acentos: **falha confirmada**;
- inspeção de contratos, prompts, quality gates e persistência: **executada**.

## Limitações da validação

A suíte pytest completa não foi executada neste ambiente porque a coleta falha por dependência ausente:

```text
ModuleNotFoundError: No module named 'redis'
```

Não foram usadas chaves reais de OpenAI, Anthropic, Gemini, Tavily ou Serper. Portanto, esta é uma auditoria estática e estrutural aprofundada, complementada por testes determinísticos locais. O primeiro passo após a correção deve ser um E2E em staging capturando o conteúdo exato dos requests enviados aos provedores.

---

## Conclusão final

A V3.5 possui boas intenções arquiteturais — contrato, dossiês, evidência, revisões e gate —, mas hoje existe uma desconexão entre a estrutura interna e o request real ao modelo. Enquanto o payload não for transmitido corretamente, os demais agentes não conseguem cumprir seus contratos.

Depois desse bloqueio, o maior risco é a **falsa sensação de segurança factual**: há IDs, scores e status de fact-check, mas vários deles são fornecidos e aprovados pelo próprio modelo sem verificação suficiente. A próxima atualização deve priorizar transporte de contexto, integridade factual determinística e aplicação real do briefing antes de ampliar funcionalidades.

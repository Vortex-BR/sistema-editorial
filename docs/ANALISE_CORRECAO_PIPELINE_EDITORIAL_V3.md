# Análise de correção — Pipeline Editorial V3

**Pacotes analisados**

- `docker-seo(2).zip` — versão original;
- `docker-seo-v3.4-atualizado.zip` — atualização resiliente;
- `bloquei.pdf` — evidência do run bloqueado.

## 1. Conclusão executiva

O bloqueio observado não é causado pelo redator. O grafo encerra a execução antes da redação quando `source_discovery` termina com `state.raw_source_documents` vazio. A transição está em `backend/app/orchestration/v3/graph.py:66-72` e gera o código `V3_NO_SOURCE_RESULTS`.

A versão V3.4 melhora significativamente a telemetria, o fallback entre Tavily/Serper e a retenção de snippets. O backend também está consistente do ponto de vista automatizado: foram executados **861 testes aprovados e 40 ignorados**.

Entretanto, a atualização ainda não deve ser tratada como correção definitiva de produção. Os testes de pesquisa usam doubles/mocks e não validam a combinação real entre idioma, mercado, provedor, qualidade das fontes, limites de API e páginas externas. Há falhas arquiteturais que podem repetir o bloqueio ou transformar a pesquisa em uma execução lenta e cara.

A correção definitiva deve ser implementada como **V3.5 — Source Discovery orientado por intenção, idioma, qualidade e cobertura**, e não apenas como mais tentativas da mesma consulta.

---

## 2. Fluxo atual do erro

1. O contrato e o plano de pesquisa são criados.
2. `source_discovery` executa as consultas.
3. Nenhum `SearchDocument` utilizável é mantido.
4. `state.raw_source_documents` fica vazio.
5. O grafo bloqueia imediatamente com `V3_NO_SOURCE_RESULTS`.
6. `source_reader`, síntese, Fact Ledger e redator nunca são executados.

A V3.4 atua principalmente entre os passos 2 e 3, mas ainda possui critérios frágeis para idioma, mercado e qualidade.

---

## 3. Correções já presentes na V3.4

### 3.1 Fallback entre provedores

`backend/app/services/editorial_v3/resilient_search.py` cria até três tentativas por consulta lógica e pode alternar Tavily e Serper.

### 3.2 Rotação de mercados

O coordenador passou a chamar `market_search_plan()` e envia `market` e `exclude_brazil` ao motor de pesquisa.

### 3.3 Assunto factual separado da palavra-chave SEO

`backend/app/services/editorial_v3/knowledge_contract.py:134-172` cria `search_subject`, evitando depender somente da palavra-chave principal.

### 3.4 Snippet como fallback

`backend/app/services/research_engine.py:515-521` e `694-699` aceitam conteúdo curto a partir de 40 caracteres, reduzindo o descarte causado por falha na leitura da página.

### 3.5 Diagnóstico de descartes

`SearchDiagnostics` registra resultados brutos, URLs inválidas, país excluído, conteúdo curto e falhas de enriquecimento.

Esses ajustes são válidos, mas não resolvem os pontos abaixo.

---

## 4. Falhas críticas restantes

## P0.1 — A consulta não é localizada para o idioma do mercado

A política define:

- Estados Unidos: `hl=en`;
- Espanha: `hl=es`;
- Suíça: `hl=de`.

Porém, a mesma consulta em português é enviada aos três mercados. Exemplo reproduzido com o código atual:

```text
US / en: germinação em Tupperware definição mecanismo evidência revisão -site:.br
ES / es: germinação em Tupperware definição mecanismo evidência revisão -site:.br
CH / de: germinação em Tupperware definição mecanismo evidência revisão -site:.br
```

Arquivos envolvidos:

- `backend/app/services/search_policy.py:16-48`;
- `backend/app/services/search_policy.py:64-91`;
- `backend/app/services/editorial_v3/resilient_search.py:154-220`.

### Impacto

O provedor recebe sinal geográfico e idioma incompatíveis com o texto pesquisado. Isso reduz recall, favorece resultados irrelevantes e pode retornar zero documentos.

### Correção obrigatória

Criar `QueryLocalizationService` com uma intenção canônica e uma consulta por mercado:

```python
LocalizedSearchIntent(
    canonical_subject="germinação de sementes de cannabis por papel-toalha em recipiente fechado",
    pt_br="germinação de sementes de cannabis com papel-toalha em recipiente fechado",
    en_us="cannabis seed germination paper towel sealed container",
    es_es="germinación de semillas de cannabis con papel de cocina en recipiente cerrado",
    de_ch="Keimung von Cannabissamen mit Papiertuch im geschlossenen Behälter",
)
```

A localização deve ocorrer uma vez por intenção de pesquisa, não a cada resultado. O sistema deve preservar os termos técnicos e registrar a consulta original e a localizada no manifesto do run.

---

## P0.2 — Mercados fixos e exclusão global do Brasil

`search_policy.py` força US, ES e CH e acrescenta `-site:.br` sempre que “Brasil” não aparece literalmente no tema/pergunta.

### Impacto

- conteúdo em português perde fontes locais válidas;
- temas jurídicos, comerciais, regulatórios ou de comportamento podem exigir o mercado do leitor;
- Suíça não é uma escolha universal para qualquer nicho;
- uma política fixa contradiz a proposta de geração premium independente do assunto.

### Correção obrigatória

Substituir a política fixa por uma estratégia derivada de:

- idioma do projeto;
- jurisdição;
- função da evidência;
- tipo de conteúdo;
- disponibilidade de fontes;
- nível de autoridade necessário.

Exemplo:

```text
terminologia/localidade     → mercado e idioma do projeto
legislação/regulação        → jurisdição obrigatória
ciência/mecanismo           → inglês global + bases científicas
procedimento técnico        → idioma local + inglês global
comparação comercial        → mercado-alvo do leitor
```

Para um projeto `pt-BR`, a busca não deve excluir o Brasil de forma global. A exclusão deve ser aplicada por tarefa, quando houver razão editorial explícita.

---

## P0.3 — O fallback para quando encontra duas fontes, mesmo que sejam ruins

Em `resilient_search.py`, a execução é interrompida quando existem dois documentos:

```python
if len(documents) >= min(minimum_results, max_results):
    break
```

O critério considera somente quantidade. Um teste reproduzido com o código atual mostrou que duas páginas de fórum com confiabilidade `0.45` encerram a busca antes de consultar o provedor alternativo.

### Impacto

O Source Discovery pode “ter sucesso”, mas o Source Reader ou a política editorial rejeitam tudo depois. O sistema apenas desloca o bloqueio para a etapa seguinte.

### Correção obrigatória

Criar `SearchAcceptancePolicy` e continuar a recuperação até que exista:

- quantidade mínima de domínios independentes;
- pelo menos uma fonte candidata institucional, científica ou técnica adequada;
- diversidade de tipos de fonte;
- ausência de domínio duplicado ou conteúdo quase duplicado;
- correspondência semântica mínima com o nó de conhecimento.

Exemplo:

```python
accepted = acceptance_policy.evaluate(
    documents,
    required_roles=task.required_source_roles,
    minimum_independent_domains=task.minimum_independent_sources,
)
if accepted.sufficient:
    break
```

---

## P0.4 — Não existe circuit breaker por provedor

Cada consulta pode gerar até três tentativas do coordenador. Cada chamada de provedor, por sua vez, pode repetir até três vezes em `_search_json()`.

Com dezenas de consultas, um Tavily indisponível pode ser tentado repetidamente antes de cada fallback para Serper.

### Impacto

- execução extremamente lenta;
- consumo desnecessário de créditos;
- risco de timeout do worker;
- projeto exibido como “em execução” por muito tempo;
- repetição de erros 401, 429 ou indisponibilidade.

### Correção obrigatória

Adicionar estado por run:

```python
ProviderCircuitState(
    consecutive_failures=0,
    disabled_until=None,
    last_error_category=None,
)
```

Regras mínimas:

- `invalid_api_key`/401: desativar o provedor imediatamente no run;
- 429: respeitar `Retry-After` e usar fallback;
- timeout/5xx: abrir circuito após duas falhas consecutivas;
- sucesso: zerar falhas;
- limite global de tentativas técnicas por run;
- timeout máximo da etapa `source_discovery`.

---

## P0.5 — O orçamento controla consultas lógicas, não chamadas reais

`maximum_search_queries` limita tarefas lógicas, mas as tentativas adicionais não entram no mesmo orçamento. Uma consulta pode gerar múltiplas requisições de busca e, no Serper, múltiplos acessos às páginas encontradas.

### Correção obrigatória

Criar um ledger de pesquisa separado:

```text
logical_queries
provider_requests
provider_retries
result_page_fetches
credits_estimated
elapsed_seconds
```

Aplicar limites configuráveis:

```text
MAX_SEARCH_PROVIDER_REQUESTS_PER_RUN
MAX_SOURCE_FETCHES_PER_RUN
MAX_SOURCE_DISCOVERY_SECONDS
MAX_SEARCH_CREDITS_PER_RUN
```

Ao atingir o limite, o sistema deve encerrar com diagnóstico claro, não permanecer indefinidamente em execução.

---

## P0.6 — O Serper faz leitura insegura e duplicada das páginas

`research_engine.py:639-720` busca o resultado orgânico, consulta `robots.txt`, baixa a página e extrai HTML. Mais tarde, `source_reader` baixa a mesma página novamente.

Problemas:

- tráfego duplicado;
- falta de limite de bytes no enriquecimento do Serper;
- `follow_redirects=True` sem validação de cada destino;
- a URL retornada pelo provedor é tratada como confiável;
- risco de SSRF para endereços locais/privados;
- um HTML muito grande pode elevar uso de memória.

### Correção obrigatória

A opção mais segura é remover o enriquecimento HTML da camada Serper:

```text
SearchEngine → título, URL, snippet e metadados
SourceReader → única camada autorizada a buscar e analisar a página
```

Caso o enriquecimento seja mantido, reutilizar o mesmo fetcher seguro do `DocumentParser`, com:

- validação DNS/IP;
- bloqueio de redes privadas e loopback;
- redirects validados individualmente;
- streaming com limite de bytes;
- limite de tempo;
- limite de concorrência por host.

---

## P0.7 — Falta um ciclo de recuperação após o Source Reader

O grafo bloqueia se `state.source_documents` ficar vazio em `graph.py:74-77`. Não existe uma volta controlada para pesquisar novamente quando:

- as páginas não podem ser lidas;
- todas as fontes são rejeitadas pela política;
- os snippets não possuem profundidade;
- faltam fontes independentes.

### Correção obrigatória

Adicionar uma transição de recuperação:

```text
source_discovery
→ source_reader
→ source_coverage_gate
   ├─ suficiente → knowledge_synthesizer
   └─ insuficiente e recovery_round < 2
      → targeted_source_recovery
      → source_reader
   └─ insuficiente e limite esgotado → blocked
```

O recovery deve receber o motivo real da rejeição e gerar consultas direcionadas, por exemplo:

- “faltou fonte institucional”;
- “faltou mecanismo científico”;
- “faltou procedimento detalhado”;
- “todos os resultados eram comerciais”;
- “as páginas bloquearam leitura”.

---

## P0.8 — Verificação de credencial dentro do run

`agent_runtime.py:612-670` verifica credenciais nunca verificadas ou com mais de 30 dias no momento da execução.

### Impacto

Uma falha transitória durante a verificação pode impedir uma credencial anteriormente funcional de participar da pesquisa. A execução fica dependente de uma chamada extra antes da chamada real.

### Correção obrigatória

Separar:

1. **preflight administrativo** — executado ao salvar/verificar a credencial;
2. **último estado conhecido** — `last_verified_at`, `last_error_code`, `verification_status`;
3. **execução real** — a chamada de busca é a validação final;
4. **falha transitória** — não apagar o último sucesso;
5. **falha definitiva 401/403** — marcar inválida e usar fallback.

A criação de um novo run deve falhar antes do dispatch somente quando nenhum provedor está configurado. Falhas temporárias devem ser resolvidas pelo fallback e pelo circuit breaker.

---

## P0.9 — Interface exibe uma regra fixa que não representa o V3

`frontend/src/pages/Pipeline.tsx:1087-1091` informa “mínimo 5 fontes distintas” de forma estática. O V3 usa requisitos por nó, claims aprovados, independência e políticas de fonte; portanto, esse texto não é necessariamente o gate real do run.

### Correção obrigatória

A interface deve ler o manifesto e exibir:

- código de bloqueio;
- etapa exata;
- provedores tentados;
- mercados e idiomas;
- consulta original e localizada;
- resultados brutos e mantidos;
- motivos de descarte;
- fontes lidas, rejeitadas e aprovadas;
- requisito real para o próximo estágio.

Quando o run estiver bloqueado, remover “AO VIVO” e qualquer agente marcado como “em execução”.

---

## 5. Correções importantes de universalidade

## P1.1 — O planner ainda usa “guia” para qualquer conteúdo

`research_planner.py` monta objetivos como:

```text
Resolver o nó ... do guia sobre ...
```

Também acrescenta termos como “guia técnico” e “manual” independentemente do tipo editorial.

### Correção

Criar vocabulário por `content_type`:

- explicativo: mecanismo, contexto, implicações;
- comparativo: critérios, diferenças, trade-offs;
- procedural: etapas, sinais, erros, correções;
- jurídico: norma, jurisdição, vigência, exceções;
- comercial: características verificáveis, comparação e adequação;
- editorial/opinião: argumentos, contrapontos e contexto.

---

## P1.2 — `search_subject` é uma concatenação com ponto e vírgula

O assunto factual aceita até 1.000 caracteres no briefing e é normalizado para até 500 caracteres durante o planejamento da pesquisa.

### Impacto

Pode gerar uma expressão longa, truncada e pouco natural. O dado serve melhor como estrutura do que como texto de busca.

### Correção

Substituir a string única por um objeto:

```python
ResearchIntent(
    entity="sementes de cannabis",
    process="germinação",
    method="papel-toalha em recipiente fechado",
    variables=["umidade", "temperatura", "ventilação"],
    desired_evidence=["mecanismo", "procedimento", "sinais", "falhas"],
)
```

As consultas devem ser compostas a partir dos campos necessários para cada nó, sem carregar todo o briefing em todas as buscas.

---

## 6. Arquitetura recomendada — V3.5

```text
1. Project Brief
2. Canonical Research Intent
3. Evidence Requirement per Knowledge Node
4. Dynamic Market and Language Plan
5. Localized Query Builder
6. Provider Preflight
7. Search Executor
   - run budget
   - circuit breaker
   - rate limit
   - provider fallback
8. Candidate Acceptance Policy
   - relevância
   - autoridade
   - independência
   - diversidade
9. Safe Source Reader
10. Source Coverage Gate
11. Targeted Recovery, no máximo 2 ciclos
12. Claim Extraction
13. Knowledge Completeness Gate
14. Writer
```

O sistema só deve bloquear depois que o plano de recuperação tiver sido esgotado e o motivo estiver claramente classificado.

---

## 7. Novos códigos de erro recomendados

```text
V3_SEARCH_CREDENTIALS_MISSING
V3_SEARCH_CREDENTIALS_INVALID
V3_SEARCH_PROVIDERS_UNAVAILABLE
V3_SEARCH_ATTEMPT_BUDGET_EXHAUSTED
V3_SEARCH_NO_CANDIDATES
V3_SOURCE_FETCH_EXHAUSTED
V3_SOURCE_POLICY_REJECTED_ALL
V3_SOURCE_DIVERSITY_INSUFFICIENT
V3_RESEARCH_COVERAGE_INCOMPLETE
```

`V3_NO_SOURCE_RESULTS` pode continuar como código público agregado, mas o diagnóstico interno deve preservar a causa específica.

---

## 8. Testes obrigatórios antes de produção

### Integração real de provedores

Executar em staging com chaves de baixa cota:

1. Tavily válido e Serper válido;
2. Tavily 401 e Serper válido;
3. Tavily 429 e Serper válido;
4. Tavily timeout e Serper válido;
5. provedor retorna lista vazia;
6. resultados com snippet, mas página bloqueada;
7. resultados somente comerciais/fóruns;
8. URLs repetidas entre mercados;
9. página com redirect para IP privado;
10. página maior que o limite.

### Idiomas e nichos

Testar pelo menos:

- português técnico;
- inglês;
- espanhol;
- conteúdo procedural;
- conteúdo explicativo;
- comparação de produtos/serviços;
- tema jurídico com jurisdição;
- tema sem relação com cultivo.

### Critérios mínimos de aceite

- nenhuma consulta em português enviada com `hl=en`, `hl=es` ou `hl=de` sem localização;
- fallback acionado por qualidade insuficiente, não apenas por zero resultados;
- provedor defeituoso desativado no run após o limite;
- Source Discovery termina dentro do tempo configurado;
- nenhuma leitura de URL privada;
- telemetria explica 100% dos descartes;
- o run só entra em `blocked` após os ciclos de recuperação;
- UI mostra o código e o motivo real;
- projeto de teste chega ao redator com claims aprovados.

---

## 9. Plano de implementação

### Fase 1 — impedir novos bloqueios opacos

- adicionar códigos específicos;
- expor telemetria completa na interface;
- corrigir status “AO VIVO” e agentes pendentes;
- adicionar timeout e limite global de tentativas.

### Fase 2 — corrigir recall e qualidade

- implementar `ResearchIntent` estruturado;
- localizar consultas;
- tornar mercados dinâmicos;
- implementar `SearchAcceptancePolicy`;
- adicionar circuit breaker.

### Fase 3 — recuperação baseada em cobertura

- criar `source_coverage_gate`;
- criar `targeted_source_recovery`;
- limitar a dois ciclos;
- bloquear somente após esgotamento.

### Fase 4 — segurança e eficiência

- remover fetch duplicado do Serper;
- centralizar fetch seguro;
- adicionar SSRF protection, limite de bytes e redirects controlados;
- registrar custos e créditos de busca.

### Fase 5 — validação real

- testes de contrato com Tavily e Serper em staging;
- canary com feature flag `EDITORIAL_SOURCE_DISCOVERY_V35_ENABLED`;
- comparar taxa de bloqueio, duração, fontes aceitas e custo por run;
- promover para produção apenas após estabilidade.

---

## 10. Resultado esperado

Após a correção, a pesquisa não dependerá de uma palavra-chave SEO enviada no idioma errado para mercados fixos. O sistema passará a:

- entender a entidade e a necessidade factual;
- escolher idioma e mercado de acordo com cada evidência;
- alternar provedores sem repetir falhas indefinidamente;
- continuar buscando quando as primeiras fontes forem fracas;
- reaprender com os motivos de rejeição;
- bloquear somente quando realmente não houver base editorial suficiente;
- explicar com precisão por que um run não avançou.

A V3.4 é uma boa base, mas a **correção definitiva é a combinação de localização, política dinâmica de mercados, critério de qualidade, circuit breaker e recuperação após leitura**.

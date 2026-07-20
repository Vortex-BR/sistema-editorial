# Relatório de implementação — Editorial Intelligence V3.5

Data: 20/07/2026

## Resultado entregue

A V3.5 transforma a descoberta de fontes em um subsistema orientado por intenção, qualidade e cobertura. O fluxo não libera redação sem evidência, mas também não bloqueia no primeiro retorno vazio do provedor.

```text
briefing
→ contrato de conhecimento
→ intenção factual canônica
→ tarefas por nó e papel de evidência
→ mercados dinâmicos
→ consultas localizadas
→ busca com orçamento e circuit breaker
→ aceitação de candidatos
→ leitura segura e limitada
→ gate de cobertura por tarefa/nó
→ recuperação direcionada, quando necessária
→ síntese e Fact Ledger
→ redação e revisões
```

## Componentes novos

### `backend/app/services/editorial_v3/research_intent.py`

- `CanonicalResearchIntent`.
- Construção auditável da intenção a partir do contrato.
- Localização determinística para `pt`, `en`, `es` e `de`.
- Preservação de entidades e termos não traduzíveis.

### `backend/app/services/editorial_v3/search_runtime.py`

- `SearchBudgetLedger`.
- `ProviderCircuitBreaker`.
- Serialização de estado para checkpoint/retomada.
- Contabilização de chamadas, retries, fetches, créditos e timeout.

### `backend/app/services/editorial_v3/search_acceptance.py`

- `CandidateAcceptanceService`.
- `SourceCoverageService`.
- Relatórios estruturados de aceitação/rejeição e cobertura.
- Autoridade obrigatória apenas quando o papel da evidência realmente a exige.

## Componentes refatorados

### Política de busca

Arquivo: `backend/app/services/search_policy.py`

- seleção de mercado por locale, jurisdição, papel de evidência e fonte requerida;
- Brasil como mercado local de projetos `pt-BR`;
- internacionalização complementar, não substitutiva;
- nenhuma exclusão global de `.br`;
- manifesto `intent-aware-search.v3.5`.

### Contrato e planner

Arquivos:

- `backend/app/services/editorial_v3/knowledge_contract.py`;
- `backend/app/services/editorial_v3/research_planner.py`.

Mudanças:

- `research_subject` factual separado da palavra-chave SEO;
- locale do projeto persistido no contrato;
- consultas específicas ao tipo editorial;
- papéis de fonte derivados do papel de evidência;
- recuperação construída a partir do motivo da rejeição.

### Engine de busca

Arquivo: `backend/app/services/research_engine.py`

- retries e tempo passam a ser retornados na telemetria;
- limite de tentativas por chamada;
- Serper deixa de baixar cada resultado orgânico;
- itens malformados são descartados individualmente, sem perder toda a resposta;
- snippets úteis são preservados;
- classificação ampliada para fontes governamentais, universitárias e científicas;
- créditos estimados seguem tentativas reais.

### Coordenação resiliente

Arquivo: `backend/app/services/editorial_v3/resilient_search.py`

- compartilha orçamento e circuitos entre tarefas;
- localiza consultas por mercado;
- alterna Tavily/Serper quando necessário;
- para por qualidade/cobertura, não por quantidade bruta;
- limita requisições pela menor disponibilidade entre requests, retries e créditos;
- aceita engines antigas injetadas sem quebrar compatibilidade;
- pontua documentos também contra as consultas traduzidas usadas na busca.

### Leitor seguro

Arquivo: `backend/app/services/editorial_v3/document_parser.py`

- leitura por streaming;
- limite rígido de bytes;
- validação SSRF de cada redirecionamento;
- interrupção de corpo acima do limite;
- fallback compatível com doubles de teste que implementam apenas `get()`.

### Grafo e executor

Arquivos:

- `backend/app/orchestration/v3/state.py`;
- `backend/app/orchestration/v3/graph.py`;
- `backend/app/orchestration/v3/executor.py`.

Novas etapas:

```text
source_discovery
→ source_reader
→ source_coverage_gate
  ├─ cobertura suficiente → knowledge_synthesizer
  └─ lacuna recuperável → targeted_source_recovery
                           → source_reader
```

O executor:

- mantém um orçamento global por run;
- persiste intenção, métricas e circuitos;
- limita documentos e leituras;
- impede fetch duplicado da mesma URL;
- mede tarefas consultadas e tarefas com candidatos separadamente;
- gera bloqueio específico somente depois dos ciclos permitidos.

### Manifesto, credenciais, API e frontend

Arquivos principais:

- `backend/app/services/execution_manifest.py`;
- `backend/app/services/agent_runtime.py`;
- `backend/app/api/routes.py`;
- `backend/app/schemas/api.py`;
- `frontend/src/pages/Pipeline.tsx`.

Mudanças:

- só credenciais ativas e verificadas entram no manifesto;
- execução não faz revalidação preventiva;
- API apresenta `v3_research_runtime` sanitizado;
- painel V3.5 mostra orçamento, provedores, circuitos, mercados, idiomas e cobertura;
- mensagens de portão refletem requisitos efetivos.

## Configuração

Novas variáveis:

| Variável | Default | Função |
|---|---:|---|
| `V3_MAX_SEARCH_PROVIDER_REQUESTS` | 96 | Requisições reais máximas aos provedores |
| `V3_MAX_SEARCH_PROVIDER_RETRIES` | 32 | Retries reais máximos |
| `V3_MAX_SEARCH_ESTIMATED_CREDITS` | 96 | Teto estimado de unidades cobradas |
| `V3_SOURCE_DISCOVERY_TIMEOUT_SECONDS` | 240 | Tempo máximo acumulado da descoberta |
| `V3_MAX_SOURCE_FETCHES` | 64 | Tentativas máximas de leitura de documentos |
| `V3_MAX_SOURCE_RECOVERY_ROUNDS` | 2 | Ciclos direcionados após o primeiro gate |
| `V3_MIN_CANDIDATE_RELEVANCE` | 0.18 | Relevância mínima do candidato para a tarefa |

Os valores são fixados no manifesto. Alterar o ambiente não muda runs já iniciados.

## Sem migration

A atualização usa o JSON de manifesto/checkpoint já existente e campos opcionais da API. Não há alteração de schema nem migration adicional.

## Procedimento de implantação

1. Faça backup do banco e das variáveis atuais.
2. Construa uma nova imagem usando o `Dockerfile` da raiz; o build executa `npm ci` e `npm run build` para o frontend.
3. Mantenha `App replicas = 1` no serviço all-in-one.
4. Configure as sete variáveis V3.5.
5. Confirme Tavily e/ou Serper como credencial ativa e verificada antes do deploy.
6. Faça deploy da imagem imutável e aguarde `/api/v1/readiness` ficar pronto.
7. Crie uma nova execução V3. Não retome o run bloqueado com manifesto V3.4.
8. No primeiro canário, inspecione o painel “Pesquisa V3.5” e confirme que consultas, requests, circuitos, fontes aceitas e cobertura são coerentes.
9. Execute pelo menos um caso local/institucional e um caso científico/internacional antes do rollout amplo.

## Critérios para considerar produção aprovada

- ao menos um provedor real entrega resultados com a chave do ambiente;
- fallback ocorre quando o primeiro provedor é deliberadamente desativado em staging;
- um erro de autenticação abre apenas o circuito do provedor afetado;
- downloads não ultrapassam o teto e não alcançam IPs privados;
- todos os nós obrigatórios passam pelo gate de cobertura;
- o redator não inicia com cobertura incompleta;
- o bloqueio final apresenta código e déficit específicos;
- custo e duração ficam dentro dos limites definidos para o ambiente.

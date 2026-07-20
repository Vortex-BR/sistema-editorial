# Validação — Editorial Intelligence V3.5

Data: 20/07/2026

## Validação executada neste pacote

### Backend — suíte focada V3.5

Comando:

```bash
cd backend
PYTHONPATH=. pytest -q \
  tests/test_search_policy.py \
  tests/test_research_engine.py \
  tests/test_editorial_v3_resilient_search.py \
  tests/test_editorial_v3_graph.py \
  tests/test_editorial_v3_research_v35.py \
  tests/test_editorial_v3_runtime_pipeline.py \
  tests/test_editorial_v3_contracts.py
```

Resultado:

```text
63 passed
```

A suíte cobre, entre outros pontos:

- mercado local e jurisdição explícita;
- localização de consultas por mercado;
- preservação de entidades;
- remoção da exclusão global `.br`;
- fallback entre provedores;
- limites de request/retry/crédito/fetch;
- circuit breaker permanente, temporário e rate limit;
- rejeição de fórum/comercial como evidência;
- exigência de autoridade apenas nos papéis corretos;
- pontuação de documentos em idioma traduzido;
- Serper sem download antecipado de páginas;
- classificação de domínios institucionais/científicos;
- leitura com limite de bytes e redirecionamento seguro;
- novas transições do grafo;
- compatibilidade com contratos e engines anteriores.

### Compilação Python

```bash
python -m compileall -q backend/app backend/tests
```

Resultado: aprovado.

### Smoke da configuração

Os defaults V3.5 foram instanciados e validados diretamente pelo `Settings`:

```text
provider requests = 96
provider retries = 32
estimated credits = 96
discovery timeout = 240s
source fetches = 64
recovery rounds = 2
minimum relevance = 0.18
```

Resultado: aprovado.

### Frontend estático

```bash
node node_modules/typescript/bin/tsc -b --pretty false
node node_modules/eslint/bin/eslint.js .
```

Resultado: TypeScript e ESLint aprovados.

## Limitações honestas desta validação

- Não foram usadas chaves reais Tavily/Serper de produção. A integração externa precisa de smoke test no ambiente do usuário.
- A suíte backend completa não pôde ser coletada neste container porque dependências de infraestrutura do projeto (`redis`, `celery` e `asyncpg`) não estavam instaladas e o ambiente não possuía acesso de rede para instalá-las. Isso gerou erros de coleta do ambiente, não falhas nos 63 testes focados executados.
- Vitest e o build Vite não puderam rodar neste host Linux porque o `node_modules` disponível no pacote original continha somente o binding nativo Windows do Rolldown. O ambiente estava sem rede para instalar o binding Linux. `tsc` e ESLint foram executados com sucesso. O Dockerfile de produção executa `npm ci` e `npm run build` em uma imagem Linux limpa, que deve ser validada no CI/deploy.
- Ruff não estava instalado neste ambiente e não pôde ser baixado. A compilação Python e a suíte focada foram executadas, mas o CI deve continuar rodando o lint oficial do repositório.

## Validação obrigatória no CI/staging

```bash
# Backend
python -m pip install -r backend/requirements.txt
cd backend
pytest -q
ruff check app tests

# Frontend
cd ../frontend
npm ci
npm test -- --run
npm run lint
npm run build
```

Também é obrigatório executar em staging:

1. busca real Tavily;
2. busca real Serper;
3. fallback com primeiro provedor indisponível;
4. resposta 401/403 e abertura de circuito;
5. resposta 429 com retry controlado;
6. página com redirect para destino privado, que deve ser rejeitada;
7. página acima do limite de bytes, que deve ser truncada/bloqueada sem esgotar memória;
8. run com fontes insuficientes, que deve recuperar e depois bloquear com déficit específico;
9. run com cobertura completa, que deve avançar para síntese e redação.

## Veredito

O pacote está validado em nível de código e testes focados para a refatoração V3.5. A liberação definitiva depende do CI completo e do smoke test com os provedores e serviços reais do ambiente de produção.

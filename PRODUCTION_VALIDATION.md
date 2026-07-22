# Validação pré-produção — Editorial V3 completa

Data: 17/07/2026

## Escopo

Esta versão adiciona o pipeline Editorial Intelligence V3 executável, mantendo a
V2 disponível por projeto. A auditoria cobre backend, frontend, schemas,
migrations, política de fontes, pesquisa estruturada, síntese procedural,
redação/revisões, qualidade, manifesto de execução e empacotamento.

## Correções e módulos validados

- Roteamento V2/V3 no Worker sem reutilizar o executor incorreto.
- Manifesto imutável com contratos pagos e skills V3 fixadas por checksum.
- Grafo com Writer inacessível antes da completude do conhecimento.
- Correção da transição entre descoberta e leitura: o gate usa documentos brutos
  descobertos, não documentos ainda não estruturados.
- Leitor de HTML/PDF com conteúdo principal, headings, listas, tabelas, canonical,
  autoria, datas, limites e proteção SSRF em cada redirect.
- IDs documentais derivados com SHA-256.
- Rejeição de e-commerce transacional e limitação de blog comercial a
  `comparison_only`.
- Conteúdo independente não é promovido a ciência por vocabulário como
  “metodologia” ou “resultados”.
- Guia técnico independente é corroborador, não autoridade absoluta automática.
- Claims com citação exata, contexto, função de evidência, condições e limites.
- Triangulação por domínio independente e pesquisa suplementar orientada por
  lacunas.
- Inventário de métodos sem nomes/aliases duplicados.
- Dossiês procedimentais, matriz condicional, links externos independentes e gate
  de completude.
- Writer procedural e três revisões separadas, com correções localizadas.
- Rubrica procedural mínima de 85%, eixo mínimo de 70% e métricas determinísticas
  de linguagem mecânica/template.
- Fonte e qualidade V3 integradas ao pacote de revisão humana existente.
- Contagem de documentos e domínios distintos corrigida no relatório de fontes.
- Endpoint de materialização informa corretamente se a execução V3 está ativa.
- Métodos obrigatórios persistidos no briefing e validados deterministicamente.
- Gate pré-Writer exige cobertura, claims, passos e referência por método.
- Writer recebe faixa adaptativa, marcação por método e uma reparação estrutural dirigida.
- Rotas OpenAI conhecidas atualizam preço e limites ao salvar, evitando custo antigo em modelo novo.
- Score geral fica limitado a 59% sempre que existir blocker crítico.
- Migrations Alembic `0029` (artefatos V3), `0030` (reparo de qualidade e perfis de modelo) e `0031` (ordem editorial, prosa observável e envelope do Writer).

## Resultados executados no código-fonte

### Backend

- Ruff: aprovado.
- `compileall`: aprovado.
- Pytest: **810 aprovados, 40 ignorados**.
- O único aviso é uma depreciação no `TestClient` dos próprios testes.
- `pip check`: nenhum requisito quebrado.
- Bandit não estava instalado nesta máquina; Ruff, compileall, testes e `pip check` foram executados.
- Alembic: head único **`0031`**.
- SQL offline `0030 -> 0031` e downgrade `0031 -> 0030`: aprovados.

### Frontend

- Frontend sem mudanças funcionais nesta versão; a suíte foi executada com as dependências já instaladas no ambiente de validação.
- ESLint: aprovado.
- Vitest: **63 testes aprovados em 8 arquivos**.
- TypeScript e build Vite: aprovados.
- `npm audit` não foi repetido nesta atualização, porque não houve alteração de dependências frontend e a validação não deve declarar resultado remoto não executado.

### Arquivos

- JSON e YAML são validados antes do empacotamento.
- O pacote final não deve conter `.env`, `node_modules`, `dist`, caches, bytecode,
  cobertura ou credenciais.
- O ZIP final é extraído em diretório limpo e revalidado antes da entrega.

## Limitações do ambiente de auditoria

Não estavam disponíveis Docker, PostgreSQL/pgvector, Redis nem Celery reais.
Também não foram fornecidas credenciais de busca e modelos. Assim, permanecem
como validação operacional obrigatória:

- cadeia completa de migrations em PostgreSQL vivo;
- integração Redis/Worker/Beat, leases, cancelamento e retomada;
- build e smoke test das imagens Docker;
- chamadas reais de busca e modelos, incluindo custo e fallback;
- benchmark editorial com múltiplas gerações e avaliação humana às cegas.

A migration `0031` foi validada por SQL offline nos dois sentidos. A cadeia histórica inteira não
pode ser gerada integralmente em modo offline porque a migration `0016` possui
backfill dependente de dados e conexão real.

## Rollout recomendado

1. Faça backup do banco e do volume de skills.
2. Mantenha as flags V3 desativadas durante o deploy.
3. Execute `alembic upgrade head` e confirme `0031`.
4. Valide `/api/v1/health` e `/api/v1/readiness`.
5. Ative as duas flags V3.
6. Gere um artigo canário e revise fontes, claims, links, texto, bloqueadores,
   tentativas e custo.
7. Aprove manualmente apenas se exigir edição leve.
8. Amplie gradualmente; preserve a V2 para rollback.

Consulte `docs/EDITORIAL_V3_PRODUCTION_RUNBOOK.md`.

# Relatório da atualização Editorial V3.7

## Objetivo

Consolidar as correções da V3.6.3 e fechar os riscos apontados na revisão técnica do Claude, com foco em:

- integridade do CI e da publicação da imagem;
- segurança de dependências e segredos;
- rotação segura da chave do cofre;
- hardening HTTP sem quebrar a criação idempotente;
- evolução do Motor de Inteligência Editorial;
- testes de regressão e documentação operacional.

A V3.7 preserva a remoção de jurisdição e de restrições editoriais baseada em território. Nenhum campo de jurisdição foi reintroduzido.

## Correções implementadas

### 1. CI e imagem de produção

- Removido o Alembic head manual e desatualizado.
- O CI resolve o único head diretamente do grafo de migrations; nesta versão, o resultado é `0036`.
- A imagem de produção é construída uma única vez.
- Trivy, SBOM e smoke test operam sobre a mesma imagem.
- A publicação apenas cria a tag GHCR e envia o mesmo image ID; não há rebuild depois do teste.
- O digest publicado é extraído e validado a partir da saída do `docker push`.
- PostgreSQL/pgvector foi fixado em `pgvector/pgvector:0.8.5-pg17` nos ambientes controlados.
- Dependências Python de runtime foram separadas das ferramentas de teste/desenvolvimento.

### 2. Segurança e supply chain

- `pip-audit` para dependências Python de runtime.
- `npm audit --omit=dev` para o frontend.
- Gitleaks para segredos versionados.
- Trivy para vulnerabilidades HIGH/CRITICAL da imagem final.
- SBOM CycloneDX da imagem testada.
- Dependabot para pip, npm, GitHub Actions e Docker.
- Política de exceção de vulnerabilidade com justificativa e prazo documentada em `SECURITY.md`.

### 3. Rotação segura do cofre

- `CredentialVault` passou a usar `MultiFernet`.
- Nova variável opcional `CREDENTIAL_MASTER_KEYS`, em ordem primária → antigas.
- Compatibilidade mantida com `CREDENTIAL_MASTER_KEY`.
- Rotação em lote com bloqueio de linhas, validação prévia de todas as credenciais, dry-run, confirmação explícita, revalidação e transação única.
- Endpoint administrativo:

```text
POST /api/v1/config/credentials/rotate-master-key
```

Dry-run:

```json
{"dry_run": true}
```

Execução:

```json
{"dry_run": false, "confirmation": "ROTATE"}
```

### 4. Hardening HTTP

- CORS limitado aos métodos realmente utilizados.
- Headers autorizados incluem `Idempotency-Key`, evitando regressão na criação atômica de projetos e runs.
- CSP adicionada em `Report-Only`, para observação antes de enforcement.
- HSTS adicionado sem `includeSubDomains` automático.

### 5. Motor de Inteligência Editorial

- O Planner pode propor perguntas emergentes depois da primeira síntese das fontes.
- As propostas são limitadas por configuração, deduplicadas e vinculadas a seções existentes.
- Uma pergunta só é aceita quando existe claim relacionado nas evidências coletadas.
- O modelo não pode tornar uma pergunta crítica apenas por autodeclaração: o motor exige alinhamento mais forte.
- A métrica lexical recebeu remoção de termos genéricos, dimensões semânticas, âncoras e rejeições de incompatibilidade.
- Casos adversariais, como temperatura versus pigmentação, receberam testes de regressão.
- O contrato do pipeline foi elevado para `editorial-v3.7`.

### 6. Processo

- Criados `CONTRIBUTING.md` e `SECURITY.md`.
- Documentado o fluxo local, integração Docker, migrations, rotação de chave, rollback e checks obrigatórios.
- Criados changelog, relatório de implementação, validação e relação de arquivos alterados.

## Novas variáveis opcionais

- `CREDENTIAL_MASTER_KEYS`: lista opcional de chaves, da primária para as antigas.
- `V3_EMERGENT_QUESTIONS_ENABLED=true`
- `V3_MAX_EMERGENT_QUESTIONS=6`

Para continuar usando uma única chave, `CREDENTIAL_MASTER_KEY` permanece suficiente. Não altere a chave atual sem seguir o procedimento de rotação documentado.

## Banco de dados

Não existe migration nova. O head permanece:

```text
0036
```

## Validação executada

- Backend: 1.032 testes coletados.
- Backend: 992 aprovados.
- Backend: 40 ignorados por dependerem de infraestrutura externa.
- Frontend: 73 testes aprovados.
- Ruff: aprovado.
- Python compileall: aprovado.
- ESLint: aprovado.
- TypeScript: aprovado.
- Build Vite: aprovado.
- `npm audit --omit=dev --audit-level=high`: zero vulnerabilidades.
- Workflow YAML: carregado e validado.
- Resolver Alembic: `0036`.
- Sintaxe do image smoke: aprovada.
- Nenhum segredo de produção conhecido ou arquivo `.env` real encontrado no pacote.

## Validação pendente no ambiente real

O primeiro deploy ainda deve confirmar:

1. todos os jobs do GitHub Actions, incluindo pip-audit, Gitleaks, Trivy, SBOM, Docker smoke e integrações;
2. upgrade/readiness em uma cópia real do PostgreSQL/pgvector;
3. Redis, Worker e Beat em execução concorrente;
4. canário V2 até estado terminal;
5. canário V3 até conteúdo final;
6. chamadas reais a OpenAI/Gemini e Tavily/Serper;
7. observação da CSP Report-Only antes de transformá-la em política obrigatória.

Nenhum sistema distribuído pode receber uma garantia honesta de “zero bug para sempre”. A V3.7 fecha os defeitos reproduzidos e os riscos estruturais identificados, adiciona fail-closed, testes e observabilidade, e impede que uma falha silenciosa seja tratada como sucesso. A promoção para produção deve ocorrer somente depois dos checks e canários acima.

## Deploy recomendado

1. Publicar o código da V3.7 no GitHub.
2. Aguardar todos os jobs do workflow.
3. Publicar no EasyPanel a imagem `sha-...` gerada pelo CI, e não reconstruir a branch.
4. Executar `alembic upgrade head` e confirmar `0036`.
5. Reiniciar App, Worker e Beat.
6. Confirmar `/api/v1/readiness` com todos os componentes `ready`.
7. Executar o preflight V2 e V3.
8. Rodar um canário V2 e um V3 até o estado final.

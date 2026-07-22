# Changelog — Editorial V3.6.2

Data: 20/07/2026

## Editorial Execution Reliability & Campaign Presets

A V3.6.2 corrige o fluxo que criava projetos sem uma execução válida e substitui
o erro genérico `Execution dependencies could not be fixed safely` por um
pré-voo explícito, reparação segura e criação transacional.

## Correções de execução

- O projeto, o evento inicial, o `PipelineRun` e o `ExecutionManifest` são
  persistidos na mesma transação quando **Iniciar após criar** está marcado.
- Se o manifesto não puder ser fixado, toda a criação é revertida. O dashboard
  não recebe mais um projeto órfão que aparenta estar no Planner sem possuir run.
- A resposta de criação informa `pipeline_run_id`, `run_created` e
  `dispatch_status`; o frontend considera erro qualquer criação imediata sem ID
  de execução.
- Falhas transitórias ao publicar no broker deixam o run durável como
  `retry_scheduled`; não obrigam o usuário a criar outro projeto.
- A retomada manual usa o mesmo comportamento resiliente e não transforma uma
  indisponibilidade momentânea do broker em perda do run.
- Requisições de criação usam `Idempotency-Key`. Uma resposta perdida pode ser
  repetida sem criar outro projeto ou outro run.
- A mesma chave não pode ser reutilizada para um payload diferente.
- Uma repetição idempotente encontra e inicia um projeto legado que exista sem
  run, em vez de retornar silenciosamente `not_started`.

## Correções de dependências

- Foi criado um inventário central de papéis por versão do pipeline.
- A V2 exige somente seus seis papéis reais.
- A V3 exige também `development_editor`, `fact_checker` e `language_editor`.
- Rotas ausentes podem ser criadas automaticamente usando um provedor LLM ativo
  e verificado, sem substituir configurações administradas existentes.
- Rotas antigas inválidas são diagnosticadas e nunca sobrescritas
  silenciosamente.
- Credenciais LLM e de pesquisa precisam estar ativas e verificadas.
- Superior Skills, skills padrão e skills V3 são verificadas antes de criar o
  run.
- O endpoint `GET /api/v1/config/execution-preflight` expõe somente códigos de
  dependência seguros e permite reparação controlada com `repair=true`.
- A readiness agora possui o componente `execution_dependencies`.
- A prontidão é calculada para a versão que será executada: uma lacuna exclusiva
  da V3 não impede um run V2 válido.
- A retomada de um run com manifesto já fixado não depende de alterações feitas
  posteriormente nas rotas mutáveis.

## Interface e recuperação

- A página de configuração mostra o estado do pré-voo e oferece
  **Verificar e corrigir**.
- Erros da API exibem `error_code` e a lista de dependências, em vez da mensagem
  genérica sem diagnóstico.
- Projetos antigos sem run aparecem como **Não iniciada**.
- A tela do projeto oferece **Iniciar execução** para projetos legados sem run e
  permite novo run depois de estados terminais recuperáveis.
- Os botões distinguem claramente **Criar e iniciar V2**, **Criar projeto V2**,
  **Criar e iniciar V3** e **Criar projeto V3**.

## Campanha pré-configurada

Foi adicionada a campanha:

`MSB — Germinação no papel-toalha`

Ela preenche em um clique o briefing completo solicitado para a Maconha Seeds
Bank, incluindo escopo, intenção, público, estratégia de busca, jurisdição,
oferta, CTA, as vinte perguntas editoriais obrigatórias e a estrutura esperada.
O sistema tenta selecionar automaticamente o perfil cujo `brand_name` é
`Maconha Seeds Bank`; os campos continuam editáveis.

## Banco de dados

- Nenhuma migration nova.
- Alembic head permanece `0035`.
- O contrato editorial V3.6.1 permanece inalterado; esta versão corrige
  orquestração, diagnóstico e experiência de criação.

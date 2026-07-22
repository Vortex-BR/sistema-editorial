# Relatório da atualização Editorial V3.6.2

## Objetivo

Corrigir os dois defeitos informados:

1. erro ao criar um projeto com **Iniciar Editorial V3 após criar** marcado;
2. projeto V2 criado sem `PipelineRun`, permanecendo no painel sem executar o
   fluxo até o conteúdo final.

Também foi adicionada a campanha pré-configurada solicitada para a Maconha Seeds
Bank.

## Causa estrutural

A criação do projeto e a criação da execução não formavam uma operação confiável
única. O projeto podia aparecer no dashboard mesmo quando o sistema falhava ao
fixar rotas, credenciais, skills ou o manifesto. Além disso, o inventário de
dependências não diferenciava adequadamente os papéis da V2 e da V3 em todos os
pontos do fluxo.

A mensagem genérica escondia a dependência exata e fazia o usuário repetir a
criação, com risco de projetos duplicados ou parados.

## Correções implementadas

### Criação atômica

Quando o início automático está marcado, projeto, evento, run e manifesto são
gravados na mesma transação. Uma falha antes do manifesto confirmado executa
rollback completo.

### Confirmação obrigatória do run

A API retorna `pipeline_run_id`, `run_created` e `dispatch_status`. O frontend
não trata a operação como sucesso quando o início foi solicitado, mas nenhum run
foi registrado.

### Pré-voo acionável

Foi criado um diagnóstico por versão que verifica:

- flags V3;
- rotas de modelos;
- credenciais LLM verificadas;
- Tavily ou Serper verificado;
- Superior Skills global e por agente;
- skills padrão;
- skills específicas V3.

A interface permite selecionar V2 ou V3 e executar **Verificar e corrigir**.

### Separação V2/V3

A V2 exige seis agentes. A V3 acrescenta Development Editor, Fact Checker e
Language Editor. Uma dependência exclusiva da V3 não impede mais um projeto V2
válido.

### Reparo seguro de rotas

Rotas faltantes podem ser criadas automaticamente usando uma credencial LLM
ativa e verificada. Rotas administradas existentes nunca são substituídas. Uma
rota inválida é listada para correção explícita.

### Idempotência

O frontend envia `Idempotency-Key` por fingerprint do formulário. Se a resposta
for perdida, a tentativa pode ser repetida sem criar outro projeto. O backend
rejeita a mesma chave quando o payload é diferente.

### Dispatch resiliente

Se o broker falhar depois do commit, o run permanece durável e o estado é
`retry_scheduled`. Beat e o ledger de dispatch assumem a nova tentativa. A
indisponibilidade não é mais apresentada como se toda a criação tivesse falhado.

### Recuperação de projetos antigos

Projetos sem último run exibem **Não iniciada** e recebem a ação **Iniciar
execução**. O briefing existente é reutilizado.

### Campanha pronta

Foi adicionada `MSB — Germinação no papel-toalha`, contendo todo o briefing
fornecido: perfil, escopo, pesquisa, público, oferta, vinte perguntas
obrigatórias e estrutura editorial. A campanha seleciona automaticamente o
perfil Maconha Seeds Bank quando ele existe.

## Fluxo esperado depois do deploy

```text
Novo conteúdo
→ aplicar campanha ou preencher briefing
→ escolher V2/V3
→ preflight da versão selecionada
→ reparação segura de rotas faltantes
→ criação transacional de projeto + run + manifesto
→ dispatch imediato ou retry durável
→ execução pelo Worker
→ atualização de eventos no painel
→ gates editoriais
→ revisão humana/finalização conforme o pipeline
```

## Validação

- 1.013 testes backend coletados;
- 973 aprovados;
- 40 ignorados por dependerem de infraestrutura externa;
- 43 testes focados na V3.6.2 aprovados;
- 72 testes frontend aprovados;
- Ruff, compileall, ESLint, TypeScript e build Vite aprovados;
- Alembic permanece em `0035`.

## Limite honesto da garantia

Não é tecnicamente possível garantir que um sistema integrado a banco, Redis,
rede e APIs externas jamais terá qualquer erro. A atualização elimina os modos
de falha identificados e, principalmente, impede que uma falha seja confundida
com sucesso ou deixe um projeto novo silenciosamente sem run.

A liberação deve passar por um teste canário real de V2 e V3 no EasyPanel, com
Worker, Beat, Redis e provedores configurados.

## Passos de deploy e teste

1. Publicar a nova versão.
2. Executar `alembic upgrade head`; o resultado continua `0035`.
3. Reiniciar App, Worker e Beat.
4. Aguardar readiness completa.
5. Abrir **Configuração → Prontidão da execução**.
6. Verificar e corrigir V2.
7. Verificar e corrigir V3.
8. Criar um conteúdo V2 de teste com início marcado.
9. Confirmar `Último run` e eventos do Planner até o estado terminal.
10. Aplicar a campanha MSB e criar um conteúdo V3.
11. Confirmar o ID do run, o manifesto e o avanço pelos gates.
12. Abrir qualquer projeto antigo sem run e clicar em **Iniciar execução**.

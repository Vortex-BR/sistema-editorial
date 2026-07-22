# Editorial V3.6.2 — confiabilidade de criação e execução

## Invariante principal

Quando o usuário solicita criação e início imediato, existem apenas dois
resultados válidos:

1. projeto, run e manifesto existem e o dispatch foi enviado ou ficou agendado
   para retry; ou
2. nada foi persistido e a resposta informa as dependências que impedem o run.

Um projeto novo sem run não é mais um resultado permitido desse endpoint.

## Fluxo de início

```text
frontend valida campos
→ preflight administrativo com reparação segura
→ POST /projects com Idempotency-Key
→ backend repete o preflight de forma autoritativa
→ transação cria projeto, evento, run e manifesto
→ commit
→ dispatch imediato
→ Beat recupera falha transitória de publicação
→ frontend abre a tela usando o pipeline_run_id confirmado
```

## Dependências por pipeline

O sistema não usa mais uma lista universal para bloquear todas as versões. A V2
é validada pelos papéis que realmente executa; a V3 inclui os três revisores do
Motor de Inteligência Editorial.

## Diagnóstico

Antes de criar conteúdo, use na interface:

```text
Configuração → Prontidão da execução → Verificar e corrigir
```

Ou consulte:

```http
GET /api/v1/config/execution-preflight?pipeline_version=v3&repair=true
```

A resposta nunca contém a chave da credencial. Ela lista somente dependências e
reparações seguras.

## Projetos legados sem run

Abra o projeto e clique em **Iniciar execução**. O backend executa o preflight da
versão gravada no projeto, cria um manifesto novo e inicia o fluxo sem exigir a
recriação do briefing.

## Campanha pré-configurada

Na página **Novo conteúdo**, selecione:

```text
MSB — Germinação no papel-toalha
```

Clique em **Aplicar campanha**, revise os campos e então use **Criar e iniciar
V3**. A campanha não contorna gates nem pesquisa; ela apenas elimina o
preenchimento repetitivo do briefing.

## Deploy

1. publique a nova imagem;
2. mantenha uma única réplica do serviço all-in-one;
3. execute `alembic upgrade head` — o resultado continua `0035`;
4. reinicie App, Worker e Beat;
5. aguarde os heartbeats;
6. execute o preflight V2 e V3;
7. faça um teste canário de cada pipeline;
8. confirme `pipeline_run_id` e eventos até o estado final.

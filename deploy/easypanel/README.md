# PostgreSQL, pgvector e EasyPanel

Este guia cobre somente o banco e o startup do deploy atual. O schema exige a
extensão `vector`: `0001_initial_schema.py` cria a extensão, `0002` cria colunas
`VECTOR` e `0003` adiciona o embedding de artigos. PostgreSQL sem pgvector não é
compatível com o estado atual do projeto.

> **Antes do deploy, configure `App replicas = 1`.** Esta é uma regra permanente
> do modo all-in-one, não apenas uma precaução durante migrations. Cada container
> inicia API, Worker, Nginx, uma execução de migrations e um Celery Beat. Uma
> segunda réplica do App criaria um segundo Beat. Não habilite autoscaling nem
> aumente a contagem de réplicas deste serviço agrupado.

O App escuta na porta interna 8080 e executa migrations, Supervisor, API,
Worker, Beat e Nginx com UID/GID 10001, sem shell de login. Configure o domínio
do EasyPanel para encaminhar a essa porta. Apenas `/var/lib/seo` é gravável pelo
usuário da aplicação; código, estáticos e skills não são graváveis, e mounts de
skills no Compose permanecem em modo somente leitura.

O GitHub Actions publica a imagem de produção somente depois de testes, lint,
build, migrations e smoke test. Em um push para `main`, a referência é
`ghcr.io/OWNER/REPOSITORY:sha-SHA_COMPLETO`. Configure o EasyPanel para usar essa
tag ou o digest correspondente; não reconstrua continuamente a branch `main` e
não use `latest`. Commit, versão de build e digest da árvore Git ficam gravados
em `/app/build-info.json` e nas variáveis da própria imagem.

O `Dockerfile` da raiz rejeita builds sem identidade imutável. O GitHub Actions
injeta `APP_COMMIT_SHA`, `APP_BUILD_VERSION` e `APP_SOURCE_DIGEST`; por
compatibilidade, o builder de fonte do EasyPanel pode fornecer seu `GIT_SHA`,
que é gravado como revisão e identificador da fonte. O fluxo operacional
recomendado continua sendo a imagem por SHA que já passou pelo smoke test,
especialmente porque variáveis do builder aparecem na linha de build.

## Versão comprovada e imagem recomendada

| Ambiente | Configuração comprovada no repositório |
|---|---|
| Docker Compose local, banco novo | `pgvector/pgvector:0.8.5-pg17` |
| Integração no GitHub Actions | `pgvector/pgvector:0.8.5-pg17` |
| EasyPanel, instalação nova | `pgvector/pgvector:0.8.5-pg17` |

A baseline comprovada é PostgreSQL 17. O ambiente validado executou PostgreSQL
17.10 e pgvector 0.8.5. A tag `0.8.5-pg17` fixa a versão da extensão;
continue consultando o banco e os relatórios do Trivy porque a imagem-base ainda
pode receber correções de distribuição. Se um ambiente existente usa outra major, não faça downgrade nem
upgrade por simples troca de tag. Primeiro identifique a major com
`SELECT version();`, preserve o volume e planeje a migração para um volume novo.

## Instalação nova

1. No mesmo projeto do EasyPanel, crie um serviço PostgreSQL persistente com a
   imagem `pgvector/pgvector:0.8.5-pg17`. Não use `postgres:17` puro.
2. Configure no serviço PostgreSQL valores próprios para `POSTGRES_DB`,
   `POSTGRES_USER` e `POSTGRES_PASSWORD`, mantendo a porta 5432 apenas na rede
   interna.
3. Crie o Redis e o serviço App na mesma rede/projeto. Copie do painel os hosts
   internos reais; não presuma o hostname pelo nome visual do serviço.
4. No serviço App, configure `ADMIN_API_TOKEN`, `CREDENTIAL_MASTER_KEY`,
   `DATABASE_URL`, `REDIS_URL`, `SUPERIOR_SKILLS_MODE=enforced` e as demais
   variáveis operacionais de `.env.easypanel.example`. Ao usar a imagem oficial,
   não sobrescreva `APP_COMMIT_SHA`, `APP_BUILD_VERSION` nem
   `APP_SOURCE_DIGEST`: os valores já foram gravados pelo CI, são fixados no
   manifesto de cada novo run e o startup rejeita divergências. Deixe os campos
   **Command** e **Arguments** vazios para não contornar o entrypoint.
5. Monte a conexão do App no formato abaixo. A senha é um placeholder e deve
   receber URL encoding quando contiver `@`, `:`, `/`, `#`, `%` ou outros
   caracteres reservados.

   ```env
   DATABASE_URL=postgresql+asyncpg://USUARIO:SENHA_URL_ENCODED@HOST_INTERNO_POSTGRES:5432/BANCO
   ```

6. Antes de definir `APP_ENV=production`, deixe no banco uma `ModelRoute`
   utilizável para cada papel editorial, com tarifas positivas de entrada e
   saída. Um fallback com provider ou modelo diferente precisa de
   `fallback_input_cost_per_million` e `fallback_output_cost_per_million`
   próprios. Cadastre também as credenciais ativas dos providers referenciados
   e as versões ativas/aprovadas do núcleo editorial. O pré-voo apenas consulta
   e descriptografa localmente esses registros; não chama APIs pagas.
7. Configure e mantenha `App replicas = 1`. O entrypoint executa
   `alembic upgrade head` e o pré-voo antes de iniciar API, Worker, o único Beat
   e Nginx. Não aumente réplicas depois das migrations: a limitação permanece
   enquanto esses processos estiverem no mesmo container.
8. Confirme nos logs que Alembic chegou ao head. A revisão head encontrada neste
   repositório é `0029`; o comando `alembic heads` na imagem implantada é a fonte
   definitiva para a revisão daquela versão.
9. Valide a conexão, a extensão e o schema com os comandos abaixo.
10. Confirme liveness e readiness pelo domínio do App. Configure o gate de
    tráfego da plataforma com `/api/v1/readiness`; `/api/v1/health` deve ser
    usado somente como liveness do processo:

   ```bash
   curl -fsS https://SEU_DOMINIO/api/v1/health
   curl -fsS https://SEU_DOMINIO/api/v1/readiness
   ```

## Validação do banco e das migrations

Execute as consultas conectado ao banco correto, sem imprimir a senha nos logs:

```sql
SELECT version();

SELECT current_database(), current_user;

SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'vector';

SELECT extname, extversion
FROM pg_extension
WHERE extname = 'vector';

SELECT version_num
FROM alembic_version;
```

`pg_available_extensions` confirma se a imagem oferece a extensão;
`pg_extension` confirma se ela está instalada no banco atual. Se for necessário
pré-instalar a extensão, faça isso uma vez com um usuário autorizado e no banco
correto:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Não edite `alembic_version` manualmente. Dentro do container App, também é
possível consultar `alembic current` e `alembic heads` a partir de
`/app/backend`.

## Startup e concorrência de migrations

`deploy/easypanel/entrypoint.sh` usa `set -eu`, executa o pré-voo estático de
produção e depois executa:

```text
alembic upgrade head
python -m app.startup
supervisord
```

Se faltar configuração, uma rota ou credencial ativa, ou uma versão editorial
utilizável, o erro lista apenas os nomes dos requisitos e o processo encerra
antes de iniciar os serviços. O mesmo ocorre se a conexão falhar, `vector` não
estiver disponível, o usuário não puder criar a extensão ou qualquer migration
falhar. O endpoint de readiness não fica pronto antes da conclusão do pré-voo
e também exige PostgreSQL, head Alembic, pgvector, Redis, broker e heartbeats
recentes de exatamente uma instância de Worker e uma de Beat. Os heartbeats são
identificados por instância e versão e usam estruturas Redis temporárias com
TTL; o PostgreSQL não é usado como relógio de processos. Enquanto duas versões
se sobrepõem durante um deploy, a readiness e o início de novos runs ficam
bloqueados até o heartbeat antigo expirar. O endpoint de health
continua sendo liveness simples. O entrypoint não implementa coordenação entre
várias réplicas e emite um evento JSON `startup.replica_policy` com a política
suportada. Mantenha o App
permanentemente em uma réplica; o log é informativo e não tenta detectar a
contagem configurada na plataforma.

Escala horizontal futura exige separar API, Worker e Beat em serviços distintos,
além de definir a responsabilidade pelas migrations. Mesmo nessa arquitetura
futura, o serviço Celery Beat deve possuir exatamente uma réplica. Esta versão
all-in-one não implementa essa separação.

## Backup mínimo obrigatório

Faça backup antes de trocar imagem, major, volume, distribuição base ou antes de
reindexar para corrigir collation.

- Gere um dump lógico em formato custom fora do container do banco:

  ```bash
  pg_dump -h HOST_INTERNO_POSTGRES -U USUARIO -d BANCO -Fc -f seo-backup-YYYYMMDD.dump
  ```

- Confirme que o arquivo existe, tem tamanho plausível e pode ser listado:

  ```bash
  pg_restore --list seo-backup-YYYYMMDD.dump
  ```

- Quando possível, teste a restauração em um banco isolado e descartável antes
  da manutenção.
- Retenha uma cópia fora do container e fora do volume que será alterado.
- Registre a imagem/major de origem e só prossiga depois de confirmar o backup.

Não coloque senha na linha de comando ou em arquivo versionado. Use o mecanismo
seguro de credenciais da ferramenta/ambiente operacional.

## Atualização e troca de imagem

### Rollout imutável do App

1. Pause a criação de conteúdo e confirme `App replicas = 1`.
2. Aguarde o workflow da revisão desejada publicar
   `ghcr.io/OWNER/REPOSITORY:sha-SHA_COMPLETO`.
3. Troque somente a referência da imagem do App pela nova tag ou digest. Preserve
   as credenciais e remova sobrescritas de `APP_COMMIT_SHA`,
   `APP_BUILD_VERSION` e `APP_SOURCE_DIGEST` do runtime.
4. Aguarde `/api/v1/readiness` confirmar migrations no head e exatamente um
   Worker e um Beat. Não inicie runs durante a sobreposição de heartbeats.
5. Compare o SHA exibido no manifesto com o SHA da imagem. Se divergir, reverta a
   referência da imagem; não force metadados via variável.
6. Verifique Gemini e Serper pela tela Configuração e execute um canário sem
   aprovação ou publicação automática antes de liberar novos conteúdos.

### Atualização dentro da mesma major

Mesmo mantendo PG17, faça backup, abra uma janela de manutenção, reduza conexões,
atualize a imagem e valide `SELECT version()`, pgvector, `alembic_version` e o
healthcheck. Mudanças na distribuição base da imagem podem alterar a versão de
collation; verifique o warning após o restart.

### Imagem oficial para pgvector na mesma major

Trocar `postgres:17` por `pgvector/pgvector:0.8.5-pg17` mantém a major e fixa a
versão da extensão, mas não elimina riscos de distribuição, bibliotecas ou
collation. Faça backup e teste a troca
com uma cópia do volume. Não faça a primeira tentativa diretamente no único
volume de produção. Depois da troca, instale/confirme `vector`, valide o schema
e trate eventual mismatch de collation antes de retomar a única réplica do App.

### Upgrade entre major versions

PG16 para PG17 não é uma atualização de tag sobre o mesmo volume. Um volume PG16
não pode ser montado diretamente pelo servidor PG17. Uma produção
existente em PG16 deve permanecer na major de origem até a janela planejada; não
faça downgrade, não retaggeie o volume e não aponte PG17 para o diretório PG16.
Use um plano
de `pg_dump`/`pg_restore` para um banco/volume novo ou `pg_upgrade` seguindo a
documentação da versão e das imagens envolvidas. Exija backup, teste de
restauração e rollback. Nunca monte um diretório de dados PG16 diretamente em um
servidor PG17 assumindo compatibilidade.

## Collation version mismatch

O warning `database has a collation version mismatch` pode aparecer após trocar
para uma imagem baseada em outra versão de glibc. Ele não deve ser ignorado
indefinidamente: índices dependentes de collation podem precisar ser
reconstruídos antes de atualizar o metadado da versão.

1. Faça e verifique o backup.
2. Abra janela de manutenção, pare App/workers e controle conexões concorrentes.
3. Confirme o banco e compare as versões:

   ```sql
   SELECT datname,
          datcollversion,
          pg_database_collation_actual_version(oid) AS actual_version
   FROM pg_database
   WHERE datname = current_database();

   SELECT pid, usename, application_name, client_addr
   FROM pg_stat_activity
   WHERE datname = current_database()
     AND pid <> pg_backend_pid();
   ```

4. Depois de revisar o impacto e garantir a janela exclusiva, reindexe o banco
   correto e só então atualize o metadado:

   ```sql
   REINDEX DATABASE nome_do_banco;
   ALTER DATABASE nome_do_banco REFRESH COLLATION VERSION;
   ```

Substitua `nome_do_banco`, execute conectado ao ambiente correto e observe que
`REINDEX DATABASE` não deve ser executado dentro de uma transação. Não rode
esses comandos cegamente em produção. Bancos grandes podem exigir estratégia de
reindexação e janela próprias; valide a documentação da major em uso.

## Troubleshooting

| Sintoma | Verificação | Correção segura |
|---|---|---|
| `extension "vector" is not available` | Consulte `pg_available_extensions` e a imagem efetiva. | Use uma imagem pgvector da mesma major do banco, após backup; não apague o volume. |
| `permission denied to create extension` | Consulte `current_user` e o owner do banco. | Um administrador autorizado instala `vector` uma vez no banco correto; depois execute Alembic com o usuário da aplicação. |
| `connection refused` | Confira saúde do PostgreSQL, porta 5432 interna e regras de rede. | Inicie/recupere o serviço e use seu endpoint interno; não publique a porta como atalho. |
| `getaddrinfo` ou host não encontrado | Compare `DATABASE_URL` com o host interno mostrado pelo EasyPanel. | Corrija somente o hostname/URL e faça redeploy; não use `localhost` entre containers. |
| Alembic não chega ao head | Compare `alembic current`, `alembic heads`, logs e `alembic_version`. | Corrija a primeira migration com erro e execute uma única instância migradora; não edite a tabela manualmente. |
| Collation mismatch | Compare `datcollversion` com a versão real. | Backup, manutenção, reindexação revisada e `REFRESH COLLATION VERSION` após o reindex. |
| Major incompatível ou diretório criado por outra versão | Execute `SELECT version()` e confira a origem do volume. | Interrompa a tentativa, preserve o serviço/volume original e restaure ou migre para um volume novo com processo planejado. |
| App reinicia durante migration | Leia o primeiro erro do entrypoint/Alembic. | Corrija conexão, privilégio, pgvector ou migration; não apague volume nem transforme falha em sucesso. |

# Relatório de correção — Editorial V3.8.1

Data: 21 de julho de 2026.

## Incidente reproduzido

A execução falhava na etapa `source_reader` ao persistir uma fonte já conhecida por outra execução. O PostgreSQL rejeitava o `INSERT` por colisão em `pk_v3_source_documents`.

A causa não era Redis, Celery, EasyPanel, SciELO ou indisponibilidade do PostgreSQL. O parser gerava um identificador estável para o conteúdo e o repositório utilizava esse identificador como chave primária global, apesar de a própria tabela representar documentos vinculados a uma execução específica.

## Modelo corrigido

O sistema agora separa duas identidades:

1. **Identidade do conteúdo no parser:** pode ser estável entre execuções.
2. **Identidade do registro persistido:** é estável durante retries, mas exclusiva por `pipeline_run_id + url_hash + content_hash`.

Essa separação preserva a deduplicação dentro de uma execução e permite que a mesma fonte participe de execuções diferentes.

## Proteção contra concorrência

A verificação prévia por `SELECT` não é suficiente quando uma tarefa é redespachada ou dois workers alcançam a mesma fonte quase simultaneamente. Por isso, a inserção também possui proteção no PostgreSQL por meio de `ON CONFLICT` sobre a constraint natural da tabela.

O fluxo aplicado é:

1. procurar o registro na execução atual;
2. calcular um ID determinístico por execução;
3. tentar inserir com proteção de conflito;
4. reler o registro vencedor;
5. normalizar `document_json.document_id` e o estado em memória com o ID persistido;
6. continuar a extração de claims usando esse mesmo ID.

## Retomada e compatibilidade

Ao carregar `state.source_documents`, o `source_reader` reconcilia cada payload com o banco. Isso impede que um checkpoint antigo mantenha um ID do parser enquanto a tabela possui outro ID.

A mesma correção foi aplicada à pesquisa suplementar, evitando que o problema reapareça depois da síntese quando o sistema procura fontes adicionais.

## Resultado esperado em produção

Depois do deploy:

- a mesma URL pode ser usada por projetos e execuções diferentes;
- retries da mesma execução não geram duplicidade;
- duas entregas concorrentes convergem para o mesmo registro;
- a etapa **Fontes** não deve mais falhar com `pk_v3_source_documents`;
- não é necessário limpar a tabela ou apagar o projeto anterior.

Uma execução que já terminou como `failed` deve ser iniciada novamente pela interface após o deploy.

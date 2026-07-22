# Changelog Editorial V3.8.1

Data: 21 de julho de 2026.

## Correção crítica

A etapa `source_reader` reutilizava `StructuredSourceDocument.document_id` como chave primária de `v3_source_documents`. Esse identificador é derivado da URL e do conteúdo e, por definição, pode se repetir entre projetos e execuções. Como a chave primária da tabela é global, a segunda execução que encontrava a mesma fonte falhava com:

```text
duplicate key value violates unique constraint "pk_v3_source_documents"
```

## Alterações

- A chave primária de `v3_source_documents` passou a ser determinística e limitada à execução:
  - `pipeline_run_id`;
  - `url_hash`;
  - `content_hash`.
- A persistência usa `INSERT ... ON CONFLICT DO NOTHING` sobre `uq_v3_source_document_run_url_content`.
- Uma entrega duplicada da mesma tarefa converge para o mesmo registro sem invalidar a transação SQLAlchemy.
- O documento estruturado é normalizado com o ID realmente persistido antes de entrar no estado do pipeline.
- Checkpoints criados por versões anteriores são reconciliados ao retomar a etapa.
- A pesquisa suplementar aplica a mesma normalização.
- Um registro já existente é atualizado de forma idempotente em vez de ser simplesmente retornado sem reconciliação.
- A avaliação de fonte volta a ser materializada mesmo quando o documento já existe.

## Compatibilidade

- Nenhuma tabela foi apagada.
- Nenhum dado existente precisa ser removido.
- Não foi criada migration nova.
- O Alembic head permanece `0036`.
- Registros antigos que usam o ID global continuam válidos e são reutilizados quando pertencem à mesma execução.

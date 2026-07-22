# Changelog — Editorial Intelligence V3.5.1 Generation Hardening

Data: 20/07/2026

## Objetivo

A V3.5.1 corrige as falhas encontradas na auditoria do sistema de geração de conteúdo da V3.5. O escopo começa no briefing e termina somente depois da aprovação do gate final: contexto enviado aos agentes, planejamento, redação, rastreabilidade factual, revisões, renderização, SEO, similaridade e persistência.

Esta versão é uma etapa de **endurecimento do pipeline atual**. Ela não implementa ainda o novo Motor de Inteligência Editorial; apenas remove os defeitos que impediriam esse motor de operar sobre uma base confiável.

## Transporte seguro dos dados da tarefa

- `AgentRuntime.call()` passa a separar:
  - `trace_input`: metadados privados para auditoria, nunca enviados automaticamente;
  - `task_data`: payload público indispensável à execução do agente.
- O `task_data` é serializado em JSON dentro de `<untrusted_task_data>` e precedido por uma política explícita que proíbe obedecer a instruções encontradas nos dados.
- Chaves com aparência de segredo, tokens, URLs de banco/Redis, Bearer tokens e padrões de API key são rejeitados antes da chamada ao provedor.
- O tamanho máximo do payload público é controlado por `AGENT_TASK_DATA_MAX_CHARACTERS`.
- O hash do `task_data` passa a fazer parte da idempotência; mudanças no contrato, claims ou rascunho invalidam o resultado anterior.
- O orçamento de saída do writer e do fact-checker é verificado antes da chamada para reduzir truncamento de JSON.

## Governança do briefing

- Novo `generation_brief` canônico, composto de briefing, perfil editorial, contrato, locale, audiência, marca, oferta, CTA, limites estruturais, política de fontes e restrições.
- Limites de palavras do briefing passam a governar a faixa do writer sem serem silenciosamente ampliados pelo mínimo global.
- `minimum_h2`, `minimum_h3`, `required_sections`, `claims_to_avoid`, link interno, keyword principal e idioma são verificados antes e depois das revisões.
- Nós opcionais e condicionais só entram quando o briefing estabelece sua aplicabilidade.
- O fechamento é preservado mesmo quando não exige pesquisa; oferta e CTA só aparecem quando declarados.
- Novo tipo `procedural_how_to` para guias de caminho único, sem comparação artificial de dois métodos.

## Integridade factual independente

- Factualidade não depende mais apenas de `is_factual` declarado pelo modelo.
- Números, negações, causalidade, comparações, recomendações e outros marcadores verificáveis ativam validações determinísticas.
- Cada frase factual do rascunho deve receber check correspondente no fact-check.
- `passed` é rejeitado quando existem checks ausentes, `unsupported`, `contradicted`, findings graves ou inconsistências entre bloco e frase.
- O texto do claim, condições, limitações, IDs e papel da evidência formam um catálogo compacto entregue ao writer e aos revisores.
- Alterações locais preservam números, negação, URLs, estrutura tipada e significado material.
- Depois da edição de linguagem, o sistema executa novo fact-check antes do gate final.
- Título, H1, headings e meta também entram na inspeção factual quando contêm afirmações verificáveis.

## Citações, claims e fontes

- Citações só são consideradas verificadas quando o trecho normalizado aparece de forma contínua ou em uma janela local com ordem preservada.
- Removido o fallback que aceitava 90% das palavras espalhadas pelo documento.
- Agrupamentos de suporte são normalizados com transliteração Unicode estável.
- Bundles incompatíveis em números, negação ou suporte lexical não são aprovados como corroboração do mesmo claim.
- HTML oculto, scripts, estilos, formulários e instruções típicas de prompt injection são removidos antes da extração.
- Fragmentos de fonte enviados aos agentes são rotulados como evidência não confiável, nunca como instruções.
- Trechos longos são selecionados por relevância para a tarefa em vez de somente pela posição inicial no documento.

## Revisões especializadas

- Rotas independentes para `development_editor`, `fact_checker` e `language_editor`.
- Skills superiores próprias para cada função.
- Contratos de revisão validam cobertura, severidade, status e integridade das alterações.
- Revisões não podem converter tabela/callout estruturado em bloco legado nem alterar a quantidade de colunas, linhas ou células.
- Mudanças que afetem números, negação, URLs ou conteúdo factual são rechecadas.

## Estrutura, SEO e referências visíveis

- Novos modelos tipados para tabelas e callouts.
- `ArticleBlock.structured_payload` preserva headers, linhas, células, título e tipo do callout.
- A finalização renderiza tabelas Markdown, callouts e referências numéricas visíveis.
- Uma seção de fontes é gerada com as URLs efetivamente utilizadas.
- Slugs usam transliteração Unicode estável; caracteres acentuados não são apagados nem colidem por simples remoção.
- Título, H1, meta description, faixa de palavras, headings e keyword recebem validação de conformidade.
- Múltiplos H1 são bloqueados.

## Persistência e publicação segura

- Conteúdo candidato permanece em `ArticleVersion` durante a execução.
- `Article.final_markdown`, `final_html` e metadados finais só são promovidos depois do quality gate.
- Uma execução bloqueada não sobrescreve artigo final anteriormente aprovado.
- Relatório de fontes inclui conformidade do briefing e análise de similaridade.
- Estado V3 registra `brief_compliance_report`, `content_similarity_report` e `human_review_package_id`.

## Similaridade e canibalização

- Novo verificador determinístico por shingles de cinco palavras.
- Comparação limitada ao mesmo perfil editorial ou, na ausência dele, ao mesmo idioma/nicho.
- Conteúdo duplicado e colisão exata da keyword principal podem bloquear a entrega.
- Similaridade com versões anteriores do mesmo artigo gera aviso, não falso bloqueio de atualização legítima.

## Migration

A migration `0033_editorial_v3_structured_blocks.py` adiciona:

```text
article_blocks.structured_payload JSONB NOT NULL DEFAULT '{}'
```

O head de readiness passa a ser `0033`.

## Compatibilidade

- A V2 continua preservada.
- A V3.5 de pesquisa orientada por intenção continua ativa.
- Blocos legados ainda são lidos; payload estruturado vazio é tratado de forma compatível.
- Runs antigos preservam seus artefatos e não devem ser retomados como V3.5.1.

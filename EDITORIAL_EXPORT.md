# Pacote editorial

`GET /api/v1/projects/{project_id}/export` gera um ZIP publicável em memória,
exige o header administrativo `X-Admin-Token` e falha fechado enquanto a versão
não possuir aprovação explícita de editor-chefe humano. O arquivo não é
persistido no container.

Uma exportação publicável também exige o selo editorial da versão e do pacote
revisado. O backend recalcula ambos os checksums antes de montar o ZIP e responde
com conflito seguro se markdown, HTML, SEO, outline, relatório, run, número da
versão ou pacote de revisão divergirem. Versões seladas e pacotes revisados são
protegidos contra `UPDATE` diretamente pelo PostgreSQL.

`GET /api/v1/projects/{project_id}/export?draft=true` gera somente um pacote de
revisão. Seu diretório, nome, metadados e `LEIA-ME-RASCUNHO.txt` o identificam
como `RASCUNHO / NÃO PUBLICAR`; ele não equivale à aprovação editorial.

Estrutura da revisão 2:

```text
projeto-slug/
  artigo.md
  artigo.html        # somente quando existe HTML final
  artigo.json
  fontes.json
  evidencias.json
  metadata.json
  manifesto-execucao.json
  LEIA-ME-RASCUNHO.txt # somente no pacote de revisão
```

- `artigo.*` contém a versão final persistida e metadados SEO selecionados.
- `fontes.json` contém apenas fontes efetivamente ligadas ao ledger do pipeline
  run exportado. Título, autor, publicação, URL/domínio, tipo, confiabilidade,
  hash, captura e método de extração vêm do `SourceSnapshot` imutável daquele
  run, nunca do cadastro agregado mutável `Source`; texto bruto, cookies e
  headers não são exportados.
- `evidencias.json` contém fatos, citações, decisões e a rastreabilidade já
  persistida no relatório do artigo.
- `metadata.json` identifica projeto, pipeline run, versão, status, idioma,
  tipo de conteúdo, decisão humana, checksums do selo, data da exportação e
  contagens.
- `manifesto-execucao.json` contém o resumo seguro do manifesto imutável do run:
  checksums/versões, rotas sem credenciais, contratos, IDs auditáveis, feature
  flags e identidade do build. Handoffs e snapshots aparecem somente por ID.

O pacote não inclui credenciais, prompts completos, conteúdo de memórias,
payloads de handoff, logs, erros, tracebacks, SQL operacional, vetores de
embedding ou metadados livres de infraestrutura.
O HTML usa uma allowlist sem scripts, formulários, eventos ou URLs ativas não
HTTP(S). O backend recusa a exportação quando detecta padrões de segredo no
conteúdo editorial.

## Selo de qualidade e custos auditáveis

A versão só pode chegar à aprovação humana depois de passar pela
`quality-rubric.v5`. O gate precede a curadoria de skills, portanto um artigo
bloqueado não vira aprendizado. O detalhe do pipeline expõe totais de tokens,
custo LLM estimado e tentativas de provider, mas o ZIP editorial não inclui
prompts, respostas brutas, credenciais ou erros internos.

# Changelog Editorial V3.9 — Pesquisa orientada por cobertura

## Problema eliminado

O pipeline V3 tratava uma quantidade global de claims aprovados como substituto de qualidade. Isso permitia dois comportamentos incorretos:

- uma execução com várias informações corretas podia ser bloqueada por não alcançar uma cota fixa;
- uma execução com muitos claims repetitivos podia ocultar a ausência de uma informação crítica do contrato.

A V3.9 substitui esse critério por cobertura verificável de cada informação necessária.

## Nova arquitetura

- Cada nó do contrato é expandido em requisitos estáveis de `question`, `knowledge`, `decision` e `completion`.
- A descoberta inicial usa consultas ligadas a esses requisitos, incluindo recuperação internacional em inglês.
- Textos compostos do contrato são divididos em unidades sem descartar frases após o primeiro ponto ou ponto e vírgula.
- O extrator recebe os requisitos ativos e deve devolver `coverage_requirement_ids` explícitos.
- Claims sem ID explícito só são associados por uma inferência conservadora, limitada ao mesmo nó e ao papel de evidência permitido.
- O gate avalia quantidade aprovada, diversidade independente, autoridade e papel de evidência para cada requisito.
- Requisitos críticos exigem duas fontes independentes.
- O valor `V3_MIN_APPROVED_CLAIMS` permanece apenas como indicador de densidade e não bloqueia mais a execução.

## Recuperação informação por informação

Quando falta suporte, o sistema:

1. tenta novamente a extração apenas dos requisitos ausentes usando as fontes já lidas;
2. gera consultas específicas para cada lacuna;
3. intercala as primeiras consultas entre diferentes lacunas para não desperdiçar o orçamento em uma única pergunta;
4. reutiliza uma fonte já conhecida quando ela passa a ser pertinente a outro requisito;
5. busca novas fontes dentro de limites próprios de rodada e consulta;
6. recalcula a cobertura antes de decidir avançar ou bloquear.

A recuperação de informação possui estado próprio e não consome as rodadas do gate posterior de inteligência editorial.

## Diagnóstico e front-end

A página do projeto agora expõe:

- cobertura geral e crítica;
- informações cobertas, parciais e ausentes;
- fatos aprovados e fontes independentes por requisito;
- motivo exato de cada pendência;
- rodada e fila de recuperação direcionada;
- códigos distintos para extração vazia, falta crítica e cobertura geral insuficiente.

## Compatibilidade

- Não há migration nova.
- O Alembic head continua `0037`.
- Checkpoints antigos recebem um requisito de cobertura seguro e continuam executáveis.
- A estrutura anterior de claims é preservada; os IDs de cobertura são armazenados em `validation_json`.

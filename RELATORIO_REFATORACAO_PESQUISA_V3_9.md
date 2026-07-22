# Relatório técnico — Refatoração profissional do fluxo de pesquisa V3.9

## Objetivo

Transformar o subsistema de pesquisa de um fluxo orientado por volume de claims em um fluxo orientado pelas informações que o artigo realmente precisa responder, mantendo rastreabilidade, política de fontes e bloqueio seguro.

## Causa raiz

O código anterior concluía descoberta, leitura e síntese mesmo quando o Fact Ledger não continha cobertura utilizável. O gate final comparava o número de claims aprovados com uma cota global. Essa regra não distinguia:

- repetição de fatos;
- ausência de uma pergunta específica;
- falta de diversidade de fonte para uma informação crítica;
- falha de extração versus rejeição pela política de evidência;
- fonte inacessível versus tópico sem cobertura.

## Implementação

### 1. Contrato de cobertura

`ResearchCoverageRequirement` representa uma unidade auditável. O planejador cria IDs determinísticos por tarefa e preserva:

- descrição da informação;
- tipo editorial;
- papéis de evidência aceitos;
- criticidade;
- mínimo de claims aprovados;
- mínimo de fontes independentes;
- termos de busca.

### 2. Planejamento de pesquisa

O planejador combina uma consulta técnica ampla com consultas específicas de requisitos. Uma consulta em inglês é reservada para aumentar o recall internacional. Consultas adicionais de abordagem, comparação e troubleshooting continuam disponíveis dentro do limite de seis por tarefa.

### 3. Extração factual

O agente pesquisador recebe somente os requisitos ativos na tentativa atual. Cada claim deve indicar quais requisitos responde e fornecer citação literal. IDs desconhecidos são rejeitados. Quando o modelo omite os IDs, a associação automática é conservadora, limitada ao mesmo nó e ao papel de evidência compatível.

### 4. Persistência e idempotência

Os IDs de cobertura são persistidos em `validation_json`. Quando um claim já existente é reutilizado, os requisitos são mesclados sem duplicação. Registros antigos continuam legíveis. Avaliações antigas inválidas não derrubam o gate; deixam de contar como suporte até serem válidas.

### 5. Gate por informação

`InformationCoverageService` calcula para cada requisito:

- status `covered`, `partial` ou `uncovered`;
- claims brutos e aprovados;
- fontes independentes e autoritativas;
- papéis de evidência encontrados;
- claims de suporte;
- razões de rejeição ou insuficiência.

A execução só avança quando todos os requisitos críticos estão cobertos e a taxa geral atinge o limite configurado. Requisitos críticos planejados exigem duas fontes independentes.

### 6. Recuperação direcionada

Antes de gastar uma nova busca, o sistema repete a extração apenas das informações ausentes nas fontes já lidas. Se ainda houver lacuna, cria uma fila por requisito. A fila intercala variantes de consulta para atender várias perguntas por rodada.

Quando o provedor devolve uma URL já conhecida para um novo requisito, isso é tratado como progresso: a fonte é reprocessada com o requisito explícito, em vez de ser descartada como duplicata.

### 7. Separação dos estados de recuperação

Foram separados:

- `information_recovery_*`: cobertura de informações obrigatórias;
- `intelligence_recovery_*`: validação posterior do motor editorial;
- `source_recovery_*`: cobertura estrutural de fontes por nó.

Uma recuperação não consome silenciosamente o orçamento lógico da outra.

### 8. Observabilidade

Novos eventos e payloads registram cobertura, requisitos ausentes, fila, rodadas, consultas executadas e fontes reaproveitadas. O front-end apresenta esses dados em linguagem operacional.

## Códigos de bloqueio

- `V3_INFORMATION_EXTRACTION_EMPTY`: nenhuma evidência foi associada aos requisitos.
- `V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE`: ao menos uma informação crítica ficou sem suporte suficiente.
- `V3_INFORMATION_COVERAGE_RATIO_INSUFFICIENT`: as críticas foram cobertas, mas a cobertura geral ficou abaixo do limite.
- `V3_INFORMATION_RECOVERY_EXHAUSTED`: as tentativas seguras de recuperação terminaram sem progresso.

## Configuração

```env
# Indicador de densidade; não é gate.
V3_MIN_APPROVED_CLAIMS=18

# Gate real da V3.9.
V3_MIN_INFORMATION_COVERAGE_RATIO=0.85
V3_MAX_INFORMATION_RECOVERY_ROUNDS=3
V3_MAX_INFORMATION_RECOVERY_QUERIES_PER_ROUND=8
```

## Implantação

1. Faça build sem cache a partir deste pacote ou publique a imagem imutável pelo CI.
2. Mantenha uma única réplica do App all-in-one.
3. Confirme `alembic current` em `0037`.
4. Inicie uma nova execução; execuções antigas permanecem para auditoria.
5. No projeto, confira o painel **Cobertura por informação** e a aba **Logs de erros**.

## Limites da validação local

Não foi executado um canário real com Gemini, mecanismos de busca, PostgreSQL, Redis, Celery e EasyPanel neste ambiente. Esses componentes dependem de credenciais e serviços de produção. A implementação contém testes determinísticos dos caminhos de planejamento, cobertura, recuperação, compatibilidade, grafo e política de fontes; o canário de staging continua obrigatório antes de promover a imagem.

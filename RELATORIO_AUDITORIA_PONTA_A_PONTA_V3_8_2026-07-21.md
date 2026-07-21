# Relatório de auditoria ponta a ponta — Editorial V3.8

Data: 21 de julho de 2026.

## Escopo

A revisão percorreu o fluxo de criação, manifesto, grafo de execução, checkpoint, runtime dos agentes, Writer, montagem do rascunho, retomada e validações finais. O foco foi impedir falhas silenciosas e garantir que a redação avance unidade por unidade sem repetir trabalho já concluído.

## Falhas identificadas

### 1. Redação monolítica

O Writer dependia de uma resposta extensa contendo o artigo completo. Uma interrupção perto do final podia obrigar a repetir toda a etapa, aumentar custo e ampliar a chance de truncamento ou inconsistência estrutural.

### 2. Checkpoints intermediários sem identidade própria

Checkpoints do mesmo estágio podiam gerar a mesma chave de idempotência. Isso criava risco de uma atualização posterior ser tratada como duplicada.

### 3. Conclusão de retry potencialmente suprimida

O evento `agent.completed` não distinguia tentativas. Uma nova tentativa válida poderia colidir com a conclusão anterior.

### 4. Retomada permissiva

O estado restaurado não verificava de forma suficiente se pertencia ao projeto e ao run atuais nem se possuía todos os artefatos obrigatórios para o estágio declarado.

### 5. Possibilidade de ciclos no grafo

Não havia limite global de transições. Uma combinação defeituosa de recuperação e roteamento poderia permanecer em loop.

### 6. Projeção de custo desproporcional

A pré-checagem de custo podia considerar o limite de saída do artigo completo mesmo para uma seção pequena, bloqueando chamadas que cabiam no orçamento real.

## Correções aplicadas

- Introdução de `V3WriterSectionOutput` e do agente `article_section:<section_id>`.
- Geração, validação e persistência de uma seção por vez.
- Distribuição determinística do orçamento de palavras entre as seções, sem forçar uma soma acima do máximo do artigo.
- Distribuição de blocos que garante o mínimo estrutural do rascunho completo e limita cada unidade para que o total possível não ultrapasse 300 blocos.
- Reparo dirigido de unidade inválida antes do checkpoint.
- Checkpoint após cada unidade concluída.
- Retomada que pula somente unidades persistidas e válidas.
- Interrupções preservam apenas unidades já validadas; uma unidade incompleta ou inválida nunca recebe checkpoint.
- Montagem determinística com um único H1, posições contínuas e IDs UUID5 estáveis.
- Validação factual por unidade e novamente no artigo completo.
- Reparo dirigido apenas quando a montagem final viola o schema e ainda existe tentativa disponível.
- Chaves de idempotência específicas para checkpoints e tentativas de agente.
- Invariantes de identidade e de artefatos obrigatórios na retomada.
- Limite configurável de transições e bloqueio de mutação indevida de estágio.
- Teste de regressão da cadeia integral do grafo, do contrato ao quality gate, exigindo término em `completed`.
- Orçamento de saída adequado ao tamanho da unidade gerada.
- Fixação do novo contrato, schemas e flags no manifesto reprodutível.

## Comportamento esperado

Para um blueprint com cinco seções, o sistema deve:

1. iniciar a seção 1;
2. validar evidências e estrutura;
3. salvar checkpoint da seção 1;
4. repetir o processo para as seções 2 a 5;
5. montar o artigo na ordem original;
6. executar diagnóstico do rascunho, revisão de desenvolvimento, fact-check, edição de linguagem, referências externas, finalização e quality gate.

Se o worker parar após a seção 3, a retomada deve validar o checkpoint e continuar pela seção 4. As três primeiras seções não devem gerar novas chamadas pagas.

## Resultado das validações

- Backend: 1.031 testes aprovados; 40 ignorados por dependências externas; 1 aviso.
- Frontend: 79 testes aprovados.
- Ruff, compileall, ESLint, TypeScript e build Vite: aprovados.
- `npm audit --omit=dev`: zero vulnerabilidades.
- `pip check` em ambiente virtual isolado: sem dependências quebradas.
- 39 arquivos YAML parseados sem erro.

## Riscos residuais e validação obrigatória

Não é tecnicamente possível garantir ausência absoluta de bugs. O ambiente local não possui Docker e não executou a topologia real com PostgreSQL, Redis, Celery, Nginx e EasyPanel. O comportamento de um modelo real também pode variar apesar do schema e dos reparos.

Antes de produção:

1. execute todos os jobs do CI;
2. construa e escaneie a imagem imutável;
3. confirme `/api/v1/readiness?pipeline_version=v3` totalmente pronto;
4. rode um canário com pelo menos cinco seções e interrompa o worker após uma seção intermediária;
5. confirme a retomada sem repetição de chamadas concluídas;
6. valide no banco a sequência de checkpoints e eventos;
7. confirme artigo final, evidências, custos e quality gate;
8. só então promova a imagem pelo SHA completo.

## Compatibilidade

Não existe migration nova; Alembic permanece em `0036`. Como a V3.8 altera contratos fixados no manifesto, runs V3 ativos de versão anterior devem terminar antes da troca de imagem ou ser reiniciados como novos runs.

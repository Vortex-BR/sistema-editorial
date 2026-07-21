# Relatório de implementação Editorial V3.7.4

## Incidente analisado

Uma execução real concluiu descoberta, leitura e recuperação de fontes, mas o painel ainda indicava dois nós pendentes por diversidade e papéis de fonte. Em seguida, o `Knowledge Synthesizer` falhou duas vezes com `builtins.TypeError`; a transação foi revertida e o Fact Ledger terminou com zero fatos.

O material disponível não continha o traceback interno. Portanto, a correção não presume uma única linha culpada: ela elimina quatro fragilidades reproduzíveis que permitiam o estado observado e adiciona diagnóstico seguro para a ocorrência remanescente.

## Causas estruturais corrigidas

### 1. Papel de fonte tratado como rótulo exato

O gate comparava `required_source_roles` com o único `source_role` classificado usando igualdade literal. Isso marcava como ausente uma capacidade já coberta por fonte equivalente ou mais forte.

Foi criada uma matriz explícita e testada de compatibilidade por capacidade. Fontes comerciais continuam sem satisfazer papéis científicos ou técnicos.

### 2. Fonte aproveitada somente pela consulta que a descobriu

O provedor associa cada resultado à consulta original. Um documento profundo pode responder a mecanismo, condição ambiental e sinal de sucesso, mas antes só contava para uma tarefa.

Após a leitura estruturada, um passe determinístico agora expande o mapa fonte→tarefa quando todos os critérios abaixo são cumpridos:

- a fonte é elegível para evidência;
- o papel de evidência da tarefa está autorizado pela avaliação da fonte;
- o papel da fonte satisfaz a capacidade solicitada;
- a relevância ponderada supera o limiar;
- o limite de novas associações por documento é respeitado.

### 3. Diversidade excessiva em nós periféricos

O planner aplicava duas fontes independentes a quase todos os nós, embora a condição de parada documentasse essa obrigação para nós críticos. Agora:

- nós `core`: duas fontes independentes;
- nós de apoio: uma fonte elegível;
- referências externas: uma fonte elegível, além do validador específico de referência.

### 4. Extração monolítica e rollback total

Um `TypeError` em um lote ou candidato podia interromper a etapa inteira. A nova estratégia:

1. tenta o lote da tarefa;
2. se houver `TypeError`, isola a extração por documento;
3. persiste os candidatos válidos dos demais documentos;
4. registra diagnóstico seguro e limitado;
5. realiza uma recuperação adicional das tarefas afetadas quando a quantidade de claims aprovados permanece abaixo do mínimo;
6. bloqueia de forma editorial e acionável caso o mínimo ainda não seja alcançado.

Exceções de cancelamento, autenticação, indisponibilidade de provedor e outros tipos não são ocultadas por esse tratamento.

## Arquivos principais

- `backend/app/services/editorial_v3/search_acceptance.py`
- `backend/app/services/editorial_v3/research_planner.py`
- `backend/app/orchestration/v3/executor.py`
- `backend/app/orchestration/v3/graph.py`
- `backend/tests/test_editorial_v3_research_recovery_v374.py`

## Banco e configuração

- Alembic head: `0036`.
- Sem migration nova.
- Sem variável de ambiente nova.
- O manifesto de uma nova execução utiliza automaticamente o código atualizado.

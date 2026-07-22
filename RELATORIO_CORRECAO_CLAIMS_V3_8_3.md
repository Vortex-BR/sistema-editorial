# Relatório de correção — Claims e Fact Ledger V3.8.3

## Diagnóstico do incidente

A execução chegou ao `knowledge_synthesizer` depois de concluir descoberta, leitura e cobertura das fontes. Apesar disso, o Fact Ledger permaneceu com zero fatos. O código tratava apenas exceções `TypeError` como condição recuperável. Uma resposta válida do modelo com `claims=[]`, uma associação existente somente pela URL original ou um descarte silencioso na persistência encerravam a etapa sem uma segunda estratégia.

Além disso, toda afirmação extraída de uma tarefa crítica era forçada a ser crítica, fazendo cada grupo exigir duas fontes independentes. O `support_group` fornecido pelo modelo era usado como identidade sem reconciliação, e fontes apenas comparativas podiam bloquear um grupo que também possuía evidência elegível.

## Solução aplicada

A V3.8.3 implementa recuperação fonte por fonte, normalização de aliases de URL, criticidade por afirmação, reconciliação semântica conservadora e aprovação baseada somente nas fontes elegíveis. Os descartes deixam de ser silenciosos e passam a alimentar o log do projeto.

A política factual não foi removida: claims críticos continuam exigindo diversidade e afirmações sem evidência continuam proibidas para a redação.

## Arquivos principais alterados

- `backend/app/orchestration/v3/executor.py`
- `backend/app/services/editorial_v3/artifact_repository.py`
- `backend/tests/test_editorial_v3_research_recovery_v374.py`
- `backend/tests/test_editorial_v3_claim_recovery_v383.py`
- `README.md`

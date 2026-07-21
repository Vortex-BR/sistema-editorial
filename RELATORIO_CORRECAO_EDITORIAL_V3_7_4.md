# Relatório de correção Editorial V3.7.4

## Problema observado

A execução chegou à síntese depois de ler 16 fontes e realizar recuperação direcionada. O diagnóstico permaneceu incompleto em dois nós por `independent_source_diversity_insufficient` e `required_source_roles_missing`. O `Knowledge Synthesizer` falhou duas vezes com `builtins.TypeError`, a transação foi revertida e o Fact Ledger ficou com zero fatos.

O PDF não contém o traceback interno; por isso não é tecnicamente correto atribuir o `TypeError` a uma única linha. A V3.7.4 corrige os estados estruturais comprovados e torna a extração resiliente à mesma classe de falha.

## Solução entregue

- compatibilidade semântica entre papéis de fonte equivalentes;
- reutilização controlada de uma fonte em todos os nós que ela realmente sustenta;
- duas fontes obrigatórias somente para nós core;
- síntese proibida enquanto o gate atual estiver incompleto;
- fallback de extração por documento quando o lote gera `TypeError`;
- preservação dos claims válidos e retry das tarefas afetadas;
- bloqueio `V3_APPROVED_CLAIMS_INSUFFICIENT` quando a evidência continuar insuficiente;
- códigos e motivos específicos preservados no painel;
- métricas seguras para localizar tarefa, nó e fase sem expor conteúdo ou segredo.

## Validação

- regressões V3.7.4: 6 aprovadas;
- núcleo Editorial V3: 141 aprovadas;
- Ruff e compileall: aprovados;
- frontend: 73 testes, ESLint, TypeScript e build aprovados;
- npm audit de produção: zero vulnerabilidades;
- Alembic: permanece `0036`.

A suíte backend completa foi iniciada, mas excedeu o limite do executor local em testes longos já existentes. O workflow do GitHub é obrigatório antes do deploy.

## Deploy

1. publicar o conteúdo da V3.7.4 no repositório;
2. aguardar todos os jobs do GitHub Actions;
3. implantar no EasyPanel a imagem imutável produzida pelo CI;
4. confirmar `alembic current` em `0036`;
5. reiniciar App, Worker e Beat;
6. confirmar readiness;
7. usar **Executar nova pesquisa** no projeto afetado.

O run antigo é imutável e continuará marcado como falha técnica para preservar auditoria. A validação deve ocorrer em um run novo.

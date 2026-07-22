# Changelog — Editorial V3.3 Universal Hierarchy

## Nova arquitetura

- contrato universal tipado de hierarquia editorial;
- cinco tipos editoriais reais;
- progressão definida antes da pesquisa;
- nós com ordem, dependências, aplicabilidade, centralidade e profundidade;
- gates determinísticos de plano e rascunho.

## V2

- Planner recebe a arquitetura antes de gerar perguntas;
- perguntas e seções declaram `node_ids`;
- cobertura de pesquisa por nó;
- Writer e Editor validados contra a hierarquia;
- reparo estrutural único e bloqueio rígido;
- perguntas ampliadas para até 16 no contrato de agente;
- prioridade persistida ampliada para 20.

## V3

- tipos não procedurais deixam de herdar matriz e métodos;
- síntese genérica por seção;
- Writer e Development Editor polimórficos;
- quality gate universal para conteúdo não procedural;
- pesos de importância e profundidade nos nós;
- `voice_override` utilizado corretamente;
- `outcome_confirmation` substitui o nome específico antigo, mantendo alias de leitura.

## Produto

- seletor de arquitetura disponível para V2 e V3;
- padrão alterado para guia explicativo;
- métodos obrigatórios aparecem somente em guias procedurais V3;
- exemplos e placeholders passam a usar domínios variados.

## Banco e operação

- migration `0032_universal_editorial_hierarchy`;
- CI, readiness e documentação atualizados para o head `0032`.

## Validação

- 832 testes backend aprovados;
- 40 testes backend ignorados por condições explícitas;
- 63 testes frontend aprovados;
- Ruff, ESLint, TypeScript e build Vite aprovados;
- 21 testes novos exclusivos da arquitetura universal.

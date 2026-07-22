# Arquitetura Editorial Universal

## Objetivo

A Arquitetura Editorial Universal define a progressão lógica do conteúdo **antes da pesquisa e antes da redação**. Ela não contém fatos de um nicho e não tenta transformar todo artigo em um guia procedural. Seu papel é determinar:

- qual transformação o conteúdo promete ao leitor;
- quais funções editoriais são necessárias para cumprir essa transformação;
- em que ordem essas funções devem aparecer;
- quais dependências existem entre elas;
- quais partes são centrais, de apoio ou periféricas;
- quais nós precisam de pesquisa e evidência;
- quais critérios determinísticos autorizam a passagem para a próxima etapa.

A implementação é compartilhada pelos pipelines V2 e V3.

## Tipos editoriais

O briefing escolhe explicitamente um dos tipos abaixo:

| Tipo | Uso principal | Progressão dominante |
|---|---|---|
| `explanatory_guide` | Explicar um conceito, sistema ou fenômeno | fundamento → contexto → mecanismo → implicações → aplicação |
| `procedural_decision_guide` | Comparar caminhos e ensinar uma execução verificável | fundamento → alternativas → requisitos → escolha → execução → sinais → problemas → resultado |
| `comparison` | Ajudar o leitor a comparar opções | fundamento → critérios → opções → comparação → adequação por cenário → decisão |
| `troubleshooting` | Diagnosticar e corrigir um problema | sintoma → linha de base → causas → diagnóstico → correções → verificação → prevenção |
| `commercial_education` | Educar antes de conectar uma oferta | problema → critérios → panorama → adequação → evidências e trade-offs → decisão → oferta |

O padrão para projetos novos é `explanatory_guide`. Regras de métodos, passos e referências por método só são ativadas em `procedural_decision_guide`.

## Contrato universal

O contrato está em `backend/app/schemas/editorial_hierarchy.py` e contém:

- `architecture_type`;
- estado inicial e final do leitor;
- nós ordenados;
- dependências;
- função universal de cada nó;
- aplicabilidade: obrigatória, condicional ou opcional;
- importância: central, apoio ou periférica;
- exigência de pesquisa;
- critérios de conclusão;
- pesos mínimos e máximos de profundidade;
- nó de fechamento.

O builder determinístico está em `backend/app/services/editorial_hierarchy.py`. Os templates definem apenas funções editoriais. Nenhum template contém exemplos ou fatos de germinação, cultivo, automóveis, telecomunicações ou qualquer outro nicho.

## Garantias determinísticas

### Antes da pesquisa

O sistema bloqueia o plano quando:

- um nó factual obrigatório não possui pergunta de pesquisa;
- uma pergunta aponta para um nó inexistente;
- o blueprint omite um nó obrigatório;
- a ordem dos nós é invertida;
- uma dependência aparece depois do nó dependente;
- uma parte periférica recebe profundidade desproporcional em relação ao núcleo.

### Antes e depois da redação

Cada bloco produzido declara `node_ids`. O gate bloqueia quando:

- há bloco não editorial sem identidade de nó;
- um nó obrigatório está ausente;
- a ordem lógica foi quebrada;
- as dependências foram invertidas;
- o fechamento aparece antes do desenvolvimento obrigatório;
- conteúdo periférico domina um nó central;
- um conteúdo não procedural inventa métodos ou uma matriz procedural.

O Writer recebe uma tentativa de reparo estrutural dirigida. Se o resultado continuar inválido, o pipeline não avança.

## Integração com o V2

O V2 agora:

1. constrói o contrato universal antes de chamar o Planner;
2. entrega ao Planner os nós, dependências e critérios;
3. exige `node_ids` nas perguntas e seções do blueprint;
4. persiste o contrato em `research_plans.hierarchy_json`;
5. persiste os vínculos em `research_questions.node_ids`;
6. mede cobertura de evidência por nó;
7. impede a redação quando nós factuais obrigatórios estão sem evidência;
8. exige `node_ids` em cada bloco do Writer;
9. executa gate e reparo estrutural;
10. repete a validação na edição antes da aprovação.

A migration `0032` cria os campos necessários e amplia a faixa de prioridade das perguntas para acomodar arquiteturas mais completas.

## Integração com o V3

O V3 continua com um grafo detalhado para guias procedurais, mas todos os nós passam a declarar também:

- `universal_role`;
- `applicability`;
- `importance`;
- `research_required`;
- pesos de profundidade.

Para tipos não procedurais, o `KnowledgeContractBuilder` deriva o grafo diretamente da Arquitetura Editorial Universal. Assim, conteúdos explicativos, comparativos, de troubleshooting e de educação comercial não são forçados a criar:

- inventário artificial de métodos;
- dossiês por método inexistente;
- passos fictícios;
- matriz de decisão procedural;
- referência externa por método.

A síntese, o Writer, o Development Editor e o Quality Gate escolhem o comportamento conforme o tipo editorial.

## Compatibilidade histórica

Os aliases `germination_confirmation` permanecem somente como compatibilidade de leitura para artefatos antigos. O campo canônico novo é `outcome_confirmation`. Esses aliases não são usados em prompts, skills, templates, regras de qualidade ou interfaces.

Runs já criados mantêm seus manifestos e contratos originais. A nova arquitetura vale para **novos runs**, preservando a reprodutibilidade de retomadas antigas.

## Testes cross-domain

`backend/tests/test_editorial_hierarchy.py` verifica a mesma infraestrutura com:

- troca de óleo do carro;
- portabilidade numérica;
- planos de internet residencial;
- bateria de notebook que não carrega;
- backup gerenciado para pequenas empresas.

Os testes cobrem:

- construção do grafo;
- ausência de termos de nicho;
- ordem e dependências;
- cobertura de pesquisa;
- cobertura do blueprint;
- bloqueio por nó ausente;
- bloqueio por ordem invertida;
- bloqueio por hierarquia de profundidade;
- contrato V3 genérico sem métodos artificiais;
- manutenção do grafo V3 procedural detalhado.

## Extensão futura

Um novo tipo editorial deve ser adicionado somente quando possuir:

1. transformação clara do leitor;
2. template universal sem fatos de nicho;
3. critérios de conclusão por nó;
4. integração com o planner, pesquisa, Writer, editor e quality gate;
5. testes em pelo menos três domínios não relacionados;
6. comportamento de fallback explícito para runs antigos.

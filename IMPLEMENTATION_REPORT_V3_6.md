# Relatório de implementação — Editorial V3.6

Data: 20/07/2026

## Objetivo desta entrega

Iniciar o Motor de Inteligência Editorial sobre a base endurecida da V3.5.1. A
entrega não tenta resolver de uma vez todos os problemas de compreensão
semântica. Ela implementa o núcleo canônico, a persistência, os gates e a
integração real com pesquisa, Writer, revisores e Quality Gate.

## Arquitetura implementada

### 1. Estado editorial canônico

`backend/app/schemas/editorial_intelligence.py` define um estado estrito com:

- objetivo e intenção editorial;
- perfil e transformação esperada do leitor;
- contexto comercial e de marca;
- restrições de geração;
- perguntas canônicas;
- planos de seção;
- grafo de evidências;
- lacunas não resolvidas;
- lifecycle, revisão, validação e checksum.

O estado não é um prompt livre. IDs, referências e relações são validados por
Pydantic para impedir claims, perguntas ou seções órfãs.

### 2. Planejamento de inteligência

Depois do `knowledge_gate`, o novo `intelligence_planner` transforma o contrato e
o briefing em:

- uma pergunta central por seção;
- perguntas de conhecimento, decisão e conclusão;
- criticidade e necessidade de pesquisa;
- responsabilidade editorial e dependências da seção;
- critérios de conclusão e conclusões proibidas.

O planejamento passa por um gate determinístico antes da pesquisa.

### 3. Pesquisa orientada pelo mapa de perguntas

O Research Planner existente continua controlando orçamento, fontes e papéis de
evidência. A V3.6 o complementa com as perguntas canônicas do motor. Cada tarefa
recebe as perguntas que precisa resolver e até duas consultas adicionais, sem
ultrapassar seis consultas nem criar tarefas extras.

### 4. Grafo de evidências

Depois da síntese, `evidence_graph_builder` conecta:

- claim aprovado;
- seção responsável;
- documento de origem;
- fato-fonte persistido;
- papel de evidência;
- condição, limitação e aplicabilidade;
- status da conclusão;
- grupo de conflito;
- perguntas que o claim pode ajudar a responder.

O vínculo com fonte é reconstruído a partir dos registros persistidos, não de IDs
inventados pelo modelo.

### 5. Política de escrita

O status da conclusão gera uma política explícita:

- `confirmed` e `well_supported` → uso direto;
- `conditional` → uso somente com condição/limitação;
- `disputed` → contexto de incerteza ou divergência;
- `insufficient_evidence` → proibido.

Cada seção recebe uma lista fechada de claims autorizados. O Writer não pode
usar um claim de outra responsabilidade editorial.

### 6. Gate de prontidão

O `intelligence_gate` bloqueia quando encontra, entre outros:

- seção de pesquisa sem claim utilizável;
- pergunta crítica sem evidência;
- claim autorizado sem fato-fonte;
- claim atribuído à seção errada;
- conflito não resolvido;
- lacuna essencial aberta.

O Writer só executa quando o lifecycle está `writer_ready`.

### 7. Redação e revisões controladas

O Writer e os três revisores recebem o mesmo payload canônico. A validação do
rascunho verifica:

- seção existente e ativa;
- frase verificável sem claim;
- claim desconhecido ou proibido;
- claim fora da seção autorizada;
- suporte lexical, números e negação entre claim e frase;
- omissão de condição/limitação;
- afirmação disputada apresentada como certeza;
- seção de pesquisa sem frase factual rastreável;
- conclusão proibida.

A inspeção ocorre no Writer, novamente após o Language Editor e novamente no
Quality Gate. Assim, uma revisão não pode remover silenciosamente condições ou
introduzir fatos novos.

### 8. Persistência e auditoria

A migration `0034` cria `editorial_intelligence_snapshots`. São salvos snapshots
nas etapas:

- `intelligence_planner`;
- `evidence_graph_builder`;
- `intelligence_gate`;
- `writer`;
- `language_editor`;
- `quality_gate`.

Cada registro contém revisão, estágio, status, JSON do estado, JSON da validação
e checksum SHA-256.

### 9. Interface e observabilidade

O painel exibe os estágios de planejamento e síntese inteligente. Eventos do
runtime registram resumo, número de perguntas, claims, fontes, conflitos,
lacunas, blockers e warnings. O relatório final de fontes inclui o resumo e a
validação final do motor.

## Compatibilidade e rollout

- V2 não foi removida.
- O executor V3.6 usa os novos estágios obrigatoriamente.
- O schema `ContentKnowledgeContract.contract_version` permanece
  `editorial-v3`, evitando uma quebra de manifests e banco.
- O pacote final informa `pipeline_contract_version=editorial-v3.6`.
- A migration `0033` continua responsável pelos blocos estruturados; `0034`
  pertence exclusivamente aos snapshots de inteligência.
- É necessário criar um run novo após o deploy.

## Limites conscientes desta primeira implementação

O núcleo determinístico está implementado, porém a qualidade semântica final
continua dependendo de calibração. Em especial, o alinhamento pergunta-claim usa
papel de evidência e sinais lexicais como primeira camada; ele gera warning quando
o alinhamento é fraco, mas ainda não substitui um classificador NLI calibrado.
Esses itens estão documentados em `VALIDATION_EDITORIAL_V3_6.md` e em
`docs/EDITORIAL_INTELLIGENCE_CORE_V1.md`.

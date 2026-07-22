# Relatório de implementação — V3.4 Resilient Source Discovery

## Causa tratada

O run analisado chegou a `Source Discovery`, recebeu zero documentos utilizáveis e, corretamente, foi bloqueado antes da redação. O defeito estava na camada de pesquisa: o V3 não executava a política de mercados do sistema, usava consultas longas baseadas principalmente na palavra-chave SEO, dependia de um único provedor e não explicava onde os resultados eram descartados.

## Arquitetura implementada

### 1. Assunto factual independente da palavra-chave

`KnowledgeContractInput.from_project()` agora cria um assunto factual limitado a 240 caracteres. O valor pode ser informado manualmente por `briefing.research_subject`; quando vazio, é montado deterministicamente com os elementos mais úteis do briefing.

### 2. Planejamento de consultas conciso

O planner mantém a hierarquia do contrato, porém substitui sacos extensos de termos por consultas curtas que combinam:

- assunto factual;
- função da evidência;
- conhecimento específico do nó;
- protocolo, sinais, limites ou solução de problemas.

### 3. Execução resiliente e limitada

Cada consulta lógica possui até três tentativas de recuperação:

1. provedor principal no mercado preferencial;
2. consulta simplificada em outro mercado;
3. provedor alternativo no mercado preferencial.

A execução para antes do limite quando já obteve documentos suficientes. O orçamento editorial continua contando consultas lógicas; as tentativas técnicas ficam registradas separadamente para auditoria.

### 4. Credenciais verificadas

O manifesto registra provedor principal e fallbacks. O runtime verifica credenciais antigas ou nunca verificadas antes da pesquisa e ignora provedores inválidos sem impedir o uso de um fallback funcional.

### 5. Diagnóstico de descarte

`ResearchEngine.search_detailed()` retorna documentos e um diagnóstico estruturado. Isso permite identificar com precisão se a ausência de fontes veio do provedor, do filtro de país, de conteúdo curto, de URLs inválidas ou de falha ao enriquecer a página.

## Arquivos principais

- `backend/app/services/editorial_v3/resilient_search.py` — coordenação de mercados, variantes e fallback;
- `backend/app/services/editorial_v3/knowledge_contract.py` — construção do assunto factual;
- `backend/app/services/editorial_v3/research_planner.py` — consultas concisas e queries de lacuna;
- `backend/app/orchestration/v3/executor.py` — integração inicial e suplementar;
- `backend/app/services/research_engine.py` — busca detalhada e telemetria;
- `backend/app/services/agent_runtime.py` — seleção e verificação de credenciais;
- `backend/app/services/credential_verification.py` — verificação Tavily;
- `backend/app/services/execution_manifest.py` — ordem reprodutível de provedores;
- `frontend/src/pages/NewProject.tsx` — override opcional do assunto factual.

## Procedimento de deploy

1. Substituir o projeto pela versão deste pacote.
2. Recriar as imagens e executar o deploy normalmente.
3. Em **Configurações → Credenciais**, confirmar Tavily e/ou Serper como ativos e usar **Verificar**.
4. Abrir o projeto bloqueado e iniciar **uma nova pesquisa/execução**.
5. Conferir no evento `v3.sources.discovered` os campos de mercados, provedores, tentativas e diagnósticos.

Não é necessário alterar tabelas nem executar uma migração adicional.

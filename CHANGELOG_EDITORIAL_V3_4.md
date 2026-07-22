# Changelog — Editorial V3.4 Resilient Source Discovery

**Data:** 20 de julho de 2026  
**Objetivo:** eliminar o bloqueio prematuro `V3_NO_SOURCE_RESULTS` causado por consultas frágeis, ausência de rotação de mercados/provedores e descarte opaco de resultados.

## Descoberta de fontes resiliente

- criado `ResilientSearchCoordinator` para executar recuperação auditável por consulta lógica;
- cada consulta pode tentar, de forma limitada, o mercado preferencial, uma variante simplificada em outro mercado e um provedor alternativo verificado;
- a política internacional passa a ser aplicada pelo V3 com rotação entre Estados Unidos, Espanha e Suíça;
- resultados brasileiros continuam excluídos por padrão e só entram quando o tópico ou a pergunta exigem Brasil explicitamente;
- URLs são deduplicadas entre mercados e provedores;
- provedores personalizados e doubles de teste continuam recebendo uma única chamada, preservando compatibilidade.

## Consultas e assunto factual

- separado o conceito de palavra-chave SEO do assunto factual usado pela pesquisa;
- o sistema monta `research_subject` com palavra-chave principal, tópico, segmento, termos relacionados, objetivo, abordagens e contexto;
- adicionado campo opcional “Assunto factual da pesquisa” no formulário de novo projeto;
- consultas do V3 foram reduzidas para frases curtas, naturais e limitadas;
- consultas suplementares usam o mesmo assunto factual e uma estratégia específica para lacunas de cobertura.

## Provedores e credenciais

- o manifesto fixa a ordem de provedores e os fallbacks disponíveis;
- o runtime seleciona credenciais ativas e verificadas na ordem definida pelo manifesto;
- credenciais sem verificação recente são verificadas antes do uso;
- Tavily agora pode ser verificado pela interface;
- a verificação Tavily usa `GET /usage`, evitando consumir crédito de pesquisa;
- quando o provedor principal não está disponível, o sistema pode continuar com o segundo provedor configurado.

## Qualidade e telemetria

- o motor de pesquisa expõe diagnósticos por tentativa: resultados brutos, documentos mantidos, conteúdo curto, país excluído, URL ausente/inválida, item inválido e falha de enriquecimento;
- snippets úteis deixam de ser descartados apenas porque a leitura integral da página falhou;
- itens malformados do provedor deixam de interromper a consulta inteira;
- Tavily recebe o país correspondente ao mercado para melhorar relevância geográfica;
- métricas iniciais e suplementares registram provedores, mercados, variantes, falhas e documentos aproveitados;
- o evento `v3.sources.discovered` passa a distinguir busca vazia de indisponibilidade técnica dos provedores.

## Compatibilidade

- nenhuma migração de banco de dados é necessária;
- projetos existentes continuam válidos;
- para aproveitar o novo contrato, o novo manifesto e o assunto factual enriquecido, deve ser criada uma nova execução após o deploy, em vez de retomar o run bloqueado antigo.

## Validação

- 901 testes backend coletados: 861 aprovados e 40 ignorados por condições explícitas;
- 67 testes frontend aprovados;
- Ruff aprovado em todo o backend;
- ESLint aprovado no frontend;
- `compileall`, TypeScript e build Vite aprovados;
- novos testes cobrem rotação de mercados, fallback Tavily → Serper, assunto factual enriquecido, verificação Tavily sem crédito, country boost e diagnóstico de respostas malformadas.

# Changelog — Editorial Intelligence V3.5

Data: 20/07/2026

## Objetivo

A V3.5 elimina o bloqueio prematuro de pesquisa observado quando o provedor devolvia resultados vazios, ilegíveis ou de baixa qualidade. A correção não enfraquece o portão editorial: ela torna a descoberta, leitura e cobertura de fontes conscientes da intenção, limitadas por orçamento, recuperáveis e auditáveis.

## Pesquisa orientada por intenção

- Novo `CanonicalResearchIntent`, separado da palavra-chave SEO.
- O assunto factual utiliza tópico, objeto, método, jurisdição, idioma e papel da evidência.
- A palavra-chave comercial não é mais enviada sozinha como objeto de pesquisa quando não descreve adequadamente o fato pesquisado.
- A intenção e sua versão são fixadas no manifesto/checkpoint para reprodução do run.

## Mercados e idiomas dinâmicos

- Removida a política fixa US/ES/CH para todos os assuntos.
- Projetos `pt-BR` pesquisam o Brasil primeiro quando a evidência é local, prática, institucional ou jurisdicional.
- Estados Unidos/inglês são acrescentados para mecanismos, riscos, limitações, comparação e literatura científica.
- Espanha e Suíça entram como corpus complementar conforme idioma e função da evidência.
- Removida a exclusão global `-site:.br`; exclusão de domínio local agora é uma política explícita do chamador.
- Cada consulta é localizada para o idioma do mercado. Entidades e termos não reconhecidos são preservados em vez de traduzidos de forma inventada.

## Planejamento de pesquisa

- Consultas menores e naturais, específicas para o tipo editorial e o papel da evidência.
- Vocabulários próprios para explicação, procedimento, comparação, troubleshooting e educação comercial.
- Consultas de recuperação passam a ser geradas a partir da lacuna real: relevância, autoridade, independência, legibilidade, diversidade ou papel de evidência ausente.
- O plano continua round-robin por nó para impedir que uma única seção consuma todo o orçamento.

## Orçamento real e circuit breaker

- Novo `SearchBudgetLedger` para controlar:
  - consultas lógicas;
  - requisições reais aos provedores;
  - retries de provedor;
  - downloads de páginas;
  - créditos estimados;
  - tempo total da descoberta.
- Novo circuit breaker por provedor:
  - erros permanentes de autenticação/configuração abrem o circuito imediatamente;
  - rate limit respeita `Retry-After` quando disponível;
  - falhas transitórias sucessivas interrompem novas chamadas ao provedor defeituoso;
  - o provedor alternativo permanece disponível enquanto seu próprio circuito estiver fechado.
- Uma consulta não pode mais multiplicar requisições sem ser contabilizada.

## Aceitação, leitura e cobertura

- A pesquisa não para ao encontrar “dois documentos quaisquer”.
- Candidatos são avaliados por relevância, tipo de fonte, papel da evidência, autoridade quando obrigatória, independência, legibilidade e duplicação.
- Fóruns, comunidades e páginas comerciais continuam úteis apenas para descoberta/contexto e não são promovidos a evidência técnica.
- O Serper preserva título, URL e snippet; a página não é baixada durante a busca.
- O `SourceDocumentParser` é o único leitor do documento, evitando download duplicado.
- Leitura HTTP agora limita bytes, valida cada redirecionamento e bloqueia destinos privados/locais em todas as etapas.
- Novo `source_coverage_gate` verifica cada tarefa/nó depois da leitura.
- Novo `targeted_source_recovery` executa até o limite configurado antes de bloquear.

## Códigos de bloqueio refinados

- `V3_SOURCE_POLICY_REJECTED_ALL`: havia candidatos, mas todos violaram a política de fonte.
- `V3_SOURCE_DIVERSITY_INSUFFICIENT`: faltou independência/diversidade exigida.
- `V3_SOURCE_FETCH_EXHAUSTED`: o orçamento de leitura acabou antes da cobertura.
- `V3_RESEARCH_COVERAGE_INCOMPLETE`: a cobertura permaneceu incompleta após a recuperação.
- O erro de credencial não é exibido quando um provedor falha, mas um fallback válido foi executado.

## Credenciais e manifesto

- O manifesto V3.5 fixa apenas credenciais ativas e previamente verificadas.
- A execução não faz uma segunda verificação artificial antes da busca.
- Chave revogada ou indisponibilidade real é detectada pela chamada e tratada pelo circuit breaker.
- Ordem de provedores, limites, mercados, versões da intenção/localização e política de busca ficam fixados por run.
- Runs V3.4 ou bloqueados preservam o manifesto antigo e devem ser substituídos por uma nova execução.

## Telemetria e interface

- API de detalhe do projeto expõe um resumo seguro do runtime de pesquisa.
- A interface mostra intenção, mercados, idiomas, provedores, consultas, requisições, retries, downloads, créditos estimados, circuitos, recuperação e cobertura.
- O aviso fixo de “mínimo 5 fontes” foi substituído pelos requisitos efetivos por nó e papel de evidência.
- Novas etapas aparecem na trilha: `source_coverage_gate` e `targeted_source_recovery`.

## Compatibilidade

- Nenhuma migration de banco de dados foi adicionada.
- A V2 permanece preservada.
- Contratos antigos do V3 continuam legíveis; campos novos possuem defaults seguros.
- Engines de busca injetadas em testes/extensões que ainda não aceitam o novo limite de tentativas continuam funcionando por fallback compatível.

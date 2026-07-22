# Relatório de implementação — Logs de erros V3.8.2

## Objetivo

Disponibilizar no próprio sistema uma visão técnica e segura das falhas de geração, evitando a necessidade de acessar o terminal do EasyPanel para cada diagnóstico.

## Fluxo implementado

Quando uma exceção técnica encerra uma execução, o worker cria uma referência de correlação e grava um registro sanitizado em uma transação independente. A página do projeto consulta o endpoint administrativo da execução selecionada e combina esse registro com as falhas já registradas pelo pipeline, agentes, provedores e eventos.

A aba apresenta os registros em ordem cronológica decrescente. Cada item pode ser expandido para inspecionar a etapa, origem, código, categoria, tentativa, provedor, resposta HTTP, SQL sem parâmetros, traceback sanitizado e metadados operacionais.

## Decisões de segurança

A interface não recebe parâmetros SQL, credenciais, tokens, senhas, conteúdo bruto de cabeçalhos nem respostas completas de modelos. O backend aplica redação novamente ao ler os registros, mesmo que o dado já tenha sido sanitizado durante a escrita. O endpoint permanece protegido pelo mesmo controle administrativo utilizado nas demais rotas sensíveis.

## Resiliência

A gravação do diagnóstico não participa da transação que está tentando registrar o estado final da execução. Dessa forma, uma segunda falha no tratamento do erro não apaga o primeiro diagnóstico. O `correlation_id` é único e torna retries idempotentes.

No front-end, respostas antigas são descartadas quando o usuário troca de projeto ou execução, evitando que logs de uma execução apareçam temporariamente em outra. Durante execução ativa, a atualização ocorre a cada 15 segundos.

## Banco de dados

A migration `0037` cria `technical_error_logs` com índices por projeto, execução, agente, etapa, gravidade e código. A referência de correlação possui constraint única. Projetos e execuções removidos excluem seus logs por cascata.

## Observação sobre histórico

A migration não reconstrói tracebacks que nunca foram persistidos. Falhas anteriores ao deploy continuam disponíveis por meio de `pipeline_runs`, `agent_runs`, `provider_attempts` e `pipeline_events`; os campos técnicos completos passam a existir para novas falhas.

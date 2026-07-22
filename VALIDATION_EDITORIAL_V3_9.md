# Validação Editorial V3.9

## Resultado local

- 193 testes direcionados de pesquisa, cobertura, claims, grafo, inteligência, política de fontes e recuperação: aprovados.
- 28 testes de configuração e manifesto de execução: aprovados.
- Total executado nesta revisão: 221 testes aprovados.
- `python -m compileall` em `backend/app`: aprovado.
- Resolução do Alembic head: `0037`.
- Verificação sintática TypeScript por `typescript.transpileModule` nos arquivos alterados: aprovada.
- Verificação de whitespace nos arquivos modificados: aprovada.
- ZIP extraído em diretório limpo e os mesmos 221 testes foram executados novamente: aprovados.
- Integridade do ZIP (`unzip -t`): aprovada.
- Varredura direta por chaves privadas e credenciais anteriormente conhecidas: nenhuma ocorrência encontrada.

## Cenários cobertos por regressão

- três informações bem suportadas passam mesmo abaixo de 18 claims;
- dezoito claims repetitivos não ocultam uma informação crítica ausente;
- requisitos rejeitados apresentam diagnóstico e consultas de recuperação;
- inferência de requisitos é limitada por semântica e papel de evidência;
- texto composto não perde informações após ponto ou ponto e vírgula;
- consultas iniciais preservam uma busca internacional em inglês;
- requisitos críticos do planejador exigem duas fontes independentes;
- consultas de recuperação são intercaladas entre lacunas;
- uma fonte existente retargeteada retorna ao leitor/extrator;
- recuperação por informação não consome o estado da recuperação de inteligência;
- checkpoints legados recebem requisito de compatibilidade;
- rotas antigas de pesquisa, source policy, source assessment, Fact Ledger e manifesto continuam passando.

## Verificações indisponíveis

A suíte backend completa não pôde ser coletada porque o ambiente não possui `asyncpg`, `celery` e outros serviços de integração. O `npm ci` também não concluiu porque o registry interno respondeu HTTP 503; por isso Vitest, ESLint e o build Vite completo não foram executados localmente. Esses itens devem rodar no CI/Dockerfile, onde as dependências fixadas são instaladas.

Não foi feita chamada paga real a um modelo nem busca externa real. A aprovação de produção depende de um canário no EasyPanel.

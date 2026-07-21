# Validação Editorial V3.8

Data da auditoria: 21 de julho de 2026.

## Resultado consolidado

```text
Backend: 1031 passed, 40 skipped, 1 warning
Frontend: 79 passed
Ruff: aprovado
compileall: aprovado
ESLint: aprovado
TypeScript: aprovado
Vite build: aprovado
npm audit --omit=dev: 0 vulnerabilidades
pip check em ambiente virtual isolado: sem dependências quebradas
YAML: 39 arquivos parseados com sucesso
```

Os 40 testes ignorados dependem de serviços externos ou infraestrutura de integração. Não houve falha de asserção na suíte executável localmente.

## Regressões específicas da V3.8

Foram adicionados testes para:

- impedir mutação direta do estágio por um nó;
- percorrer a cadeia completa de geração, revisão e quality gate até `completed`;
- encerrar loop de recuperação ao atingir o limite de transições;
- gerar chaves distintas para checkpoints de cada seção;
- montar o artigo em ordem, com posições contínuas e identificadores determinísticos;
- rejeitar checkpoint pertencente a outro projeto ou run;
- rejeitar progresso que declare uma seção concluída sem o payload correspondente;
- gerar e persistir uma seção por chamada;
- retomar o Writer sem repetir seções concluídas;
- preservar no checkpoint somente as unidades concluídas quando o provedor é interrompido;
- impedir checkpoint de unidade que permanece inválida após o reparo;
- rejeitar ordem de retomada que não seja prefixo do blueprint;
- distribuir faixas de palavras sem ultrapassar o orçamento total do artigo;
- garantir pelo menos dez blocos no rascunho montado e impedir que a soma das unidades ultrapasse o limite de 300 blocos;
- reparar uma unidade inválida antes do checkpoint;
- fixar `writer_section` e `writer_section_repair` no manifesto de contratos pagos.

## Validações estáticas e de dependências

- `ruff check app tests`: aprovado.
- `python -m compileall -q backend/app`: aprovado.
- `npm audit --omit=dev`: nenhuma vulnerabilidade encontrada.
- `pip check` foi executado em um ambiente virtual criado apenas com `backend/requirements-dev.txt`: nenhuma dependência quebrada.
- `pip-audit` não concluiu porque o ambiente de auditoria não conseguiu resolver `pypi.org`. Portanto, esta validação não afirma ausência total de vulnerabilidades conhecidas nas dependências Python.

## Limites do ambiente

Docker não está disponível no executor utilizado. Por isso, não foram iniciados PostgreSQL, Redis, Celery Worker, Celery Beat, Nginx ou a imagem all-in-one do EasyPanel. Também não foi realizada chamada paga real a um provedor de modelo.

Antes da promoção para produção, a imagem deve passar pelo CI com os serviços reais e por um canário em staging conforme o runbook.

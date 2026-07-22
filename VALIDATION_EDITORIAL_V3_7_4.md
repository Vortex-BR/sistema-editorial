# Validação Editorial V3.7.4

## Testes executados

### Regressões específicas da correção

```text
6 passed
```

Cobertura:

- compatibilidade entre papéis de fonte;
- ausência de falso `required_source_roles_missing`;
- associação cruzada somente a nós relevantes;
- uma fonte para nós de apoio e duas para nós core;
- preservação de código específico no grafo;
- isolamento de `TypeError` em lote e persistência dos documentos válidos.

### Núcleo Editorial V3

```text
141 passed
```

Comando executado sobre os testes `test_editorial_v3*.py`, `test_editorial_intelligence_v37.py` e `test_editorial_intelligence_v361.py`.

### Qualidade estática do backend

```text
Ruff: aprovado
compileall: aprovado
```

### Frontend

```text
Vitest: 73 passed
ESLint: aprovado
TypeScript: aprovado
Vite build: aprovado
npm audit --omit=dev: 0 vulnerabilidades
```

## Suíte backend completa

A coleta encontrou 1.004 testes sem os grupos de integração. Duas tentativas da suíte completa ultrapassaram o limite do executor local durante testes de longa duração já existentes; não houve falha de asserção antes do timeout. Por honestidade, a V3.7.4 não declara a suíte completa como concluída localmente.

O GitHub Actions continua sendo a validação obrigatória do conjunto completo e dos testes com PostgreSQL, Redis, Celery e imagem Docker.

## Validação obrigatória após deploy

1. todos os jobs do GitHub Actions devem ficar verdes;
2. `/api/v1/readiness` deve indicar todos os componentes como `ready`;
3. criar uma nova pesquisa V3 com o mesmo briefing;
4. confirmar cobertura sem os falsos códigos de papel/diversidade;
5. confirmar Fact Ledger maior que zero;
6. confirmar avanço até os dossiês e a redação;
7. guardar o run antigo somente para auditoria, sem retomá-lo.

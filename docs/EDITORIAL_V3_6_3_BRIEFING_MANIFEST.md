# Editorial V3.6.3 — Briefing e manifesto

## Fluxo de criação

```text
campanha ou briefing manual
→ validação dos limites no navegador
→ preflight da versão
→ validação Pydantic
→ criação transacional de projeto + run
→ snapshot do manifesto sem segredos
→ dispatch
```

## Política de pesquisa

A pesquisa não usa mais um campo de jurisdição. A ordem de mercados é calculada por:

1. idioma/locale do projeto;
2. função da evidência;
3. necessidade de fontes científicas ou comparativas;
4. fallbacks internacionais limitados pelo orçamento.

## Política de segredos

O manifesto pode conter identificadores, nomes de provedores e metadados de verificação. Não pode conter:

- chave de API;
- senha;
- token de acesso/refresh;
- header Bearer;
- URL autenticada de PostgreSQL ou Redis;
- valor criptografado de credencial.

O diagnóstico mostra o caminho do campo, sem reproduzir o valor.

## Deploy

```bash
cd /app/backend
alembic upgrade head
alembic current
```

Resultado esperado: `0036`.

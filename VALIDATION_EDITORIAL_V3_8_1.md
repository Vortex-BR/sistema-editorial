# Validação Editorial V3.8.1

Data: 21 de julho de 2026.

## Resultado consolidado

```text
Backend: 1034 passed, 40 skipped, 1 warning
Testes diretamente relacionados: 49 passed
Frontend: 79 passed
Ruff: aprovado
Python compileall: aprovado
ESLint: aprovado
TypeScript/Vite build: aprovado
npm audit --omit=dev: 0 vulnerabilidades
```

## Regressões adicionadas

- o ID persistido é igual em retries da mesma execução;
- a mesma fonte recebe IDs diferentes em execuções diferentes;
- a instrução SQL contém proteção `ON CONFLICT`;
- registros legados são reconciliados sem novo `INSERT`;
- `document_json.document_id` passa a refletir a chave primária real;
- a suíte de pesquisa, recuperação, pipeline V3 e hardening continua aprovada.

## Limites da validação

O ambiente local não possui um PostgreSQL de produção nem Docker/EasyPanel disponíveis para um teste canário completo. A instrução PostgreSQL foi compilada e os testes automatizados passaram, mas o deploy deve ser validado com uma nova execução real após a atualização da imagem.

# Política de segurança

Não publique vulnerabilidades, tokens, URLs com senha ou chaves do cofre em issues públicas.

## Controles automatizados

O CI V3.7 executa:

- `pip-audit` sobre dependências Python de runtime;
- `npm audit --omit=dev` sobre dependências do frontend;
- Gitleaks sobre o histórico disponível;
- Trivy na imagem exata que será publicada;
- geração de SBOM CycloneDX;
- smoke test fail-closed da imagem.

## Exceções temporárias

Uma exceção de vulnerabilidade deve registrar: CVE, componente, motivo de não explorabilidade ou ausência de correção, compensações, responsável e data de expiração. Exceções sem prazo não são aceitas.

## Segredos

- Use o cofre de credenciais da aplicação.
- Use `CREDENTIAL_MASTER_KEYS` para rotação gradual.
- Nunca registre plaintext, ciphertext completo ou valores de variáveis secretas.
- Se um segredo for exposto, revogue-o; removê-lo apenas do último commit não é suficiente.

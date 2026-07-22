# Relatório de implementação — Editorial V3.6.3

## Problemas reproduzidos

1. A campanha pré-configurada preenchia `research_subject` acima do máximo de 240 caracteres.
2. O formulário ainda exigia/exibia uma camada de jurisdição que não era desejada.
3. O manifesto classificava `credential_verification_required_before_activation` como segredo porque o detector analisava palavras genéricas na chave, não se a chave realmente armazenava um segredo.

## Implementação

### Briefing

- `research_subject` aceita até 1.000 caracteres na API e no formulário.
- O construtor factual usa até 500 caracteres normalizados para pesquisa.
- A interface mostra contador do campo.
- A campanha MSB é validada automaticamente em teste contra os limites ativos.

### Remoção de jurisdição

- Removida do estado React, payload, schema Pydantic, contrato de conhecimento, persistência, intenção canônica e política de mercados.
- Mercados são selecionados por locale e papel de evidência.
- Contratos antigos com a chave `jurisdiction` são normalizados antes da validação para evitar quebra de retomada.
- Migration `0036` remove a coluna legada.

### Manifesto

- O detector agora usa nomes exatos e sufixos que efetivamente representam segredos.
- Metadados de verificação de credencial são permitidos.
- `api_key`, tokens, senhas, URLs de banco e valores Bearer continuam bloqueados.
- O erro registra somente `manifest_path:<caminho>` como dependência acionável.

## Compatibilidade

- Projetos novos não possuem jurisdição.
- Briefings antigos enviados pela API têm o campo extra ignorado.
- Estados antigos de contrato são normalizados.
- A migration deve ser aplicada antes da ativação da imagem.

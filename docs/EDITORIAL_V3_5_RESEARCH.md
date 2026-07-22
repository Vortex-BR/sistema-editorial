# Pesquisa V3.5 — arquitetura e operação

## Princípio

Uma palavra-chave SEO não é necessariamente um objeto factual. A V3.5 cria uma intenção canônica antes da pesquisa e usa essa intenção para decidir idioma, mercado, consulta, fonte esperada e critério de cobertura.

## Fluxo

```text
contrato editorial
→ intenção factual canônica
→ tarefa de pesquisa por nó
→ seleção de mercados
→ localização da consulta
→ busca limitada e resiliente
→ aceitação de candidatos
→ leitura segura
→ cobertura por nó
→ recuperação dirigida
→ síntese
```

## Como o mercado é escolhido

1. jurisdição explicitamente pedida;
2. mercado do locale do projeto;
3. inglês/Estados Unidos para mecanismo, risco, limitação, comparação ou literatura científica;
4. corpus complementar em espanhol ou alemão conforme necessidade;
5. no máximo três mercados por consulta lógica por padrão.

O Brasil não é excluído de projetos em português. Uma fonte brasileira pode ser a fonte correta para regulamentação, disponibilidade local, terminologia, instituições ou procedimento contextualizado.

## Como a consulta é localizada

A tradução é determinística. O sistema traduz termos editoriais/técnicos conhecidos e preserva nomes próprios, marcas, cultivares, siglas e termos desconhecidos. Se não houver tradução suficiente, a consulta é reconstruída a partir do assunto canônico e do papel de evidência, em vez de enviar uma frase portuguesa inalterada sob `hl=en`, `hl=es` ou `hl=de`.

## Orçamento

O orçamento diferencia:

- consulta lógica: uma pergunta planejada;
- request: uma chamada HTTP ao provedor;
- retry: uma chamada adicional após falha;
- fetch: download/leitura de uma fonte;
- crédito estimado: unidade de consumo do provedor;
- timeout: tempo acumulado da fase.

O executor bloqueia novas chamadas quando qualquer teto aplicável é atingido e registra `exhausted_by` no runtime.

## Circuit breaker

Cada provedor possui circuito independente. Autenticação inválida e configuração permanente abrem o circuito imediatamente. Falhas transitórias repetidas abrem temporariamente. Rate limit usa pausa controlada. Um circuito aberto não invalida o outro provedor.

## Aceitação do candidato

A URL só entra no conjunto útil quando possui relevância suficiente para a tarefa. Depois, a política verifica:

- tipo e confiabilidade da fonte;
- independência;
- papel de evidência;
- autoridade, quando obrigatória;
- duplicação por URL/domínio/conteúdo;
- legibilidade disponível.

Quantidade bruta não é critério de sucesso.

## Leitura segura

- apenas HTTP/HTTPS;
- resolução/host verificados contra rede privada/local;
- cada redirect é validado;
- resposta lida por streaming;
- limite de bytes aplicado durante a transferência;
- conteúdo não é baixado duas vezes pelo Serper e pelo leitor;
- URL já processada não é lida novamente no mesmo run.

## Gate e recuperação

O `source_coverage_gate` mede cada tarefa/nó. Lacunas produzem motivos estruturados, como relevância, autoridade, independência, diversidade, legibilidade ou papel ausente. O `targeted_source_recovery` cria novas consultas a partir desses motivos e retorna ao leitor. Após o limite de rounds, o run bloqueia com código específico e preserva todo o diagnóstico.

## Diagnóstico operacional

No painel “Pesquisa V3.5”, observe:

- intenção e locale;
- mercados e idiomas usados;
- provedor por tentativa;
- consultas lógicas versus requests reais;
- retries e créditos estimados;
- circuitos abertos e motivo;
- candidatos recebidos, aceitos e rejeitados;
- tentativas de leitura;
- round de recuperação;
- tarefas/nós ainda sem cobertura.

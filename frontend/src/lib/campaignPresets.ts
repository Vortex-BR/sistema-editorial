export type CampaignPreset = {
  id: string
  label: string
  description: string
  preferredBrand: string
  values: Record<string, string | boolean>
}

const additionalContext = `O artigo deve ser claro, didático e aprofundado, usando linguagem acessível sem infantilizar o leitor. Explique termos técnicos na primeira vez em que aparecerem.

Evite introduções genéricas, repetições, frases promocionais exageradas e informações apresentadas como certeza quando houver divergência entre as fontes.

A pesquisa deve priorizar materiais técnicos, universidades, instituições agrícolas, bancos de sementes reconhecidos e publicações especializadas. Informações comerciais não devem ser utilizadas como única evidência para recomendações técnicas.

O conteúdo deve obrigatoriamente responder:

1. O que acontece biologicamente quando uma semente começa a germinar?
2. Quais materiais são adequados para o método?
3. O recipiente precisa ser hermeticamente fechado?
4. Qual deve ser o nível de umidade do papel?
5. Como diferenciar papel úmido de papel encharcado?
6. Qual faixa de temperatura é recomendada pelas fontes?
7. A semente deve permanecer no escuro?
8. Com que frequência o recipiente deve ser verificado?
9. Qual é o cronograma típico de germinação?
10. Como identificar uma semente saudável em processo de abertura?
11. Quando a raiz principal está desenvolvida o suficiente para a transferência?
12. Como segurar e transferir a semente sem danificar a raiz?
13. Qual deve ser a orientação da raiz ao colocá-la no substrato?
14. O que fazer quando o papel começa a secar?
15. O que fazer quando há condensação excessiva?
16. Como prevenir mofo e contaminações?
17. Quando uma semente lenta ainda pode ser considerada viável?
18. A imersão prévia por aproximadamente três horas possui fundamento técnico?
19. Em quais situações a imersão pode aumentar riscos?
20. Quais são os erros que mais causam perda de sementes?

Estrutura editorial esperada:

- H1 claro e alinhado à palavra-chave principal;
- introdução que responda rapidamente ao problema;
- explicação breve sobre o processo de germinação;
- lista de materiais;
- preparação do recipiente e do papel;
- avaliação da imersão prévia;
- procedimento em etapas;
- condições ambientais;
- rotina de monitoramento;
- sinais de germinação saudável;
- momento e procedimento de transferência;
- erros comuns e soluções;
- perguntas frequentes;
- fechamento informativo com CTA discreto.

Não inventar temperaturas, prazos, percentuais ou taxas de sucesso. Toda recomendação numérica deve estar ligada a evidência verificável. Quando fontes confiáveis divergirem, apresentar a divergência e explicar o que é consenso e o que depende do contexto.`

export const CAMPAIGN_PRESETS: CampaignPreset[] = [
  {
    id: 'msb-germinacao-papel-toalha',
    label: 'MSB — Germinação no papel-toalha',
    description: 'Briefing V3 completo para o guia de germinação em recipiente plástico.',
    preferredBrand: 'Maconha Seeds Bank',
    values: {
      editorial_pipeline_version: 'v3',
      start_immediately: true,
      name: 'Guia de germinação de sementes de cannabis no papel-toalha dentro de recipiente plástico',
      topic: 'Como germinar sementes de cannabis utilizando papel-toalha umedecido dentro de um recipiente plástico fechado, controlando umidade, temperatura, ventilação, higiene e o momento correto de transferência para o substrato.',
      content_objective: 'Ensinar o leitor a compreender e executar corretamente o método do papel-toalha dentro de um recipiente plástico, desde a preparação dos materiais até a transferência segura da semente germinada para o substrato. O conteúdo deve explicar o motivo de cada etapa, os sinais de progresso, os erros mais comuns e as medidas preventivas para reduzir ressecamento, encharcamento, mofo, danos à raiz e perda da semente.',
      search_intent: 'informational',
      segment: 'Germinação de sementes de cannabis',
      editorial_content_type: 'explanatory_guide',
      reader_start_state: 'O leitor possui sementes de cannabis, mas não sabe como utilizar corretamente o método do papel-toalha dentro de um recipiente plástico. Ele pode ter dúvidas sobre a quantidade de água, o tipo de papel, a necessidade de manter o recipiente fechado, a faixa de temperatura, a exposição à luz, a frequência de monitoramento e o momento adequado para transferir a semente.\n\nTambém pode ter enfrentado problemas anteriores, como sementes que não abriram, papel ressecado, excesso de água, aparecimento de mofo, raiz quebrada durante o manuseio ou transferência prematura para o substrato.',
      reader_final_state: 'Ao terminar o guia, o leitor deve conseguir preparar o recipiente, umedecer corretamente o papel-toalha, posicionar as sementes, manter condições ambientais estáveis, monitorar a germinação sem manipulação excessiva, identificar sinais de problemas e reconhecer o momento adequado para transferir a semente germinada ao substrato sem danificar a raiz principal.',
      article_promise: 'Entregar um guia completo, claro e tecnicamente fundamentado sobre a germinação de sementes de cannabis no papel-toalha dentro de um recipiente plástico.\n\nO conteúdo deve explicar os fundamentos biológicos da germinação, os materiais necessários, a preparação do ambiente, o controle de umidade e temperatura, a função do recipiente fechado, a necessidade ou não de escuridão, a rotina de monitoramento e os critérios visuais para a transferência.\n\nTambém deve avaliar criticamente a prática de deixar a semente em água por aproximadamente três horas antes de colocá-la no papel-toalha, explicando quando essa etapa pode ser opcional, quais seriam seus possíveis objetivos e por que ela não deve ser apresentada como regra universal sem evidência.\n\nO artigo deve diferenciar papel úmido de papel encharcado, indicar como evitar falta de oxigênio e proliferação de fungos, explicar como manusear a raiz principal e apresentar soluções para sementes lentas, papel seco, condensação excessiva e sinais de deterioração.',
      scope_limit: 'O conteúdo deve terminar após a transferência da semente recém-germinada para o substrato inicial e os primeiros cuidados imediatamente posteriores.\n\nNão deve avançar para cultivo vegetativo, fertilização, iluminação de crescimento, treinamento de plantas, floração, colheita, rendimento, aumento de potência ou produção comercial.\n\nO guia deve permanecer concentrado no método do papel-toalha dentro de recipiente plástico. Outros métodos podem ser mencionados apenas para contextualização breve, sem transformar o artigo em uma comparação completa.',
      jurisdiction: 'Brasil. O conteúdo deve possuir finalidade educativa e informativa. Deve orientar o leitor a verificar as leis, autorizações e restrições aplicáveis à germinação e ao cultivo de cannabis em sua jurisdição antes de executar qualquer procedimento. Não apresentar o conteúdo como autorização legal.',
      requires_method_comparison: false,
      requires_external_reference_per_method: true,
      primary_keyword: 'como germinar semente de cannabis no papel-toalha',
      research_subject: 'Germinação de sementes de cannabis utilizando papel-toalha umedecido dentro de recipiente plástico fechado, incluindo absorção inicial de água, controle de umidade, temperatura, disponibilidade de oxigênio, prevenção de fungos, cronograma de germinação, desenvolvimento da raiz principal e transferência segura para o substrato.',
      secondary_keywords: 'germinação de sementes de cannabis\ngerminar semente de maconha\ngerminação no papel-toalha\ngerminação em recipiente plástico\ngerminação em Tupperware\npapel-toalha úmido para germinação\nquanto tempo demora para germinar\ntemperatura para germinação\numidade para germinação\nsemente de cannabis não germina\ncomo evitar mofo na germinação\nraiz principal da semente\nmomento de transferir a semente\ncomo plantar semente germinada\nimersão prévia da semente\nquebra de dormência de sementes\nsemente germinada no papel\nerro na germinação de sementes',
      niche: 'Sementes de cannabis, germinação e cultivo inicial',
      audience: 'Adultos iniciantes e cultivadores com alguma experiência que procuram informações claras e tecnicamente fundamentadas sobre germinação de sementes de cannabis. O público inclui pessoas que nunca utilizaram o método do papel-toalha e leitores que já tentaram germinar sementes, mas enfrentaram falhas como ressecamento, excesso de água, mofo ou danos durante a transferência.',
      reader_age_min: '24',
      reader_age_max: '50',
      reader_life_stage: 'Jovem adulto, adulto, jardineiro e cultivador doméstico',
      reader_knowledge_level: 'mixed',
      reader_context: 'O leitor provavelmente já possui ou pretende adquirir sementes e quer evitar desperdícios durante a germinação. Ele procura um procedimento organizado e compreensível, mas encontra informações contraditórias sobre deixar a semente de molho, quantidade de água, temperatura, luz, recipiente fechado, ventilação e tamanho da raiz antes do plantio.\n\nSeus principais receios são perder sementes, provocar mofo, deixar o papel secar, afogar a semente, quebrar a raiz principal ou transferir a semente cedo ou tarde demais.',
      reader_goal: 'Um procedimento confiável e fácil de acompanhar que explique não apenas o que fazer, mas por que cada etapa é necessária. O leitor espera saber quais materiais utilizar, como preparar o papel-toalha, como posicionar e armazenar as sementes, quando abrir o recipiente, como identificar problemas e qual é o sinal visual adequado para realizar a transferência.',
      language: 'pt-BR',
      offer: 'Catálogo de sementes de cannabis da Maconha Seeds Bank e conteúdos educativos complementares sobre conservação, germinação e cuidados iniciais.',
      commercial_objective: 'Atrair tráfego orgânico qualificado por meio de um conteúdo útil e confiável sobre germinação de sementes de cannabis, fortalecer a autoridade editorial da Maconha Seeds Bank e apresentar o catálogo de sementes de forma contextual, discreta e não invasiva.',
      desired_action: 'Conheça o catálogo de sementes da Maconha Seeds Bank e consulte as opções disponíveis.',
      additional_context: additionalContext,
      required_methods: '',
      required_approach_type: 'method',
    },
  },
]

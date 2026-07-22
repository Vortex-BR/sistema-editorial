from app.services.editorial_v3.prose_quality import analyze_editorial_prose


def test_prose_quality_detects_numeric_opening_and_summary_like_body():
    markdown = """
# Como germinar sementes com segurança

## Condições essenciais para a germinação

Durante a germinação, manter a umidade entre 70% e 90% ajuda o processo. A temperatura ideal fica entre 21°C e 29°C. Nos primeiros dias, use 20°C a 25°C.

## Luz, escuridão e ventilação

A semente se desenvolve em ambiente escuro e morno. A exposição direta à luz não é necessária.

## Principais riscos

O excesso de umidade reduz o oxigênio. O excesso de umidade favorece problemas secundários.

## Avaliação das sementes

Sementes saudáveis tendem a ser firmes. Sementes frágeis podem apresentar rachaduras.

## Quando transferir

Quando a raiz atingir 1 a 2 cm, faça a transferência. Quando surgirem folhas, observe a muda.

## Manuseio

Evitar raízes submersas reduz riscos. Evitar pressão sobre a raiz reduz danos.

## Checklist

Manter umidade. Controlar temperatura. Evitar encharcar.
"""

    result = analyze_editorial_prose(
        markdown,
        method_labels=[
            "papel-toalha",
            "copo com água",
            "jiffy",
            "plantio direto",
        ],
    )

    assert result["premature_numeric_density"] is True
    assert result["opening_method_mention_count"] == 0
    assert result["heading_body_imbalance"] is True
    assert result["summary_like_compression"] is True
    assert result["observable_naturalness_score"] < 0.7


def test_prose_quality_accepts_oriented_and_developed_body():
    markdown = """
# Como escolher um método de germinação sem perder o controle do processo

Quem começa costuma olhar apenas para o momento em que a raiz aparece, mas a primeira decisão vem antes: escolher onde a semente ficará enquanto absorve água. Papel-toalha e copo com água deixam a mudança visível; jiffy e plantio direto reduzem a necessidade de transferir a semente depois. Essa diferença muda o tipo de acompanhamento, não cria um vencedor universal.

A escolha fica mais simples quando o leitor separa duas perguntas. Ele quer observar cada mudança ou prefere mexer o mínimo possível? E consegue manter o meio úmido sem deixá-lo saturado? Com esse mapa em mente, as condições ambientais deixam de parecer uma lista de números e passam a ter função dentro de cada método.

## O que os quatro métodos têm em comum

A água inicia a mudança, mas umidade não é sinônimo de encharcamento. O meio precisa conservar contato suficiente para evitar ressecamento e, ao mesmo tempo, permitir troca de ar. No papel-toalha, isso depende da quantidade de água retida no material; no jiffy, da forma como o torrão foi hidratado e drenado. O princípio é o mesmo, embora o controle prático seja diferente.

Temperatura e higiene entram como condições de estabilidade. Em vez de corrigir o ambiente a cada hora, o leitor precisa evitar oscilações bruscas e reduzir fontes de contaminação no recipiente e nas mãos. Essa preparação parece secundária, porém evita que um método simples seja prejudicado por uma variável que nada tem a ver com a qualidade da semente.

## Papel-toalha: mais observação, mais manuseio

O método permite acompanhar a abertura sem retirar a semente do material. Primeiro se prepara uma superfície limpa e úmida; depois a semente é acomodada sem pressão. O recipiente ajuda a desacelerar a perda de água, mas precisa ser verificado porque condensação excessiva e material encharcado indicam que o equilíbrio se perdeu.

A vantagem de enxergar a raiz vem acompanhada de uma responsabilidade: transferir sem puxar, dobrar ou deixar a estrutura exposta por tempo desnecessário. Por isso, o momento de avanço não depende apenas do relógio. Ele depende do sinal observado e da capacidade de preparar o destino antes de tocar na semente.

## Plantio direto: menos transferência, menos visibilidade

No plantio direto, o ambiente final já recebe a semente. Isso elimina uma transferência, mas também reduz a possibilidade de acompanhar o que acontece abaixo da superfície. O controle passa a ser feito pelo estado do substrato e pelo surgimento da plântula, não pela inspeção constante da raiz.

Esse caminho faz sentido para quem consegue preparar um meio leve e manter a umidade com pequenas correções. Se a superfície seca rapidamente, a solução não é compensar com uma grande quantidade de água de uma vez; é ajustar a frequência e proteger a estabilidade do ponto onde a semente foi colocada.
"""

    result = analyze_editorial_prose(
        markdown,
        method_labels=[
            "papel-toalha",
            "copo com água",
            "jiffy",
            "plantio direto",
        ],
    )

    assert result["opening_method_mention_count"] >= 2
    assert result["premature_numeric_density"] is False
    assert result["summary_like_compression"] is False
    assert result["heading_body_imbalance"] is False
    assert result["severe_mechanical_prose"] is False
    assert result["observable_naturalness_score"] >= 0.7

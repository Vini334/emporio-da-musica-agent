# Relatório de qualidade de dados

> Gerado por `python -m emporio.data.prepare` (não editar à mão). Data de referência para atrasos: **02/11/2026**.

O bruto (`data/raw`) fica intocado. A camada `data/processed` só **padroniza** (tipos, enums, specs parseadas, campos normalizados para busca) e **marca** registros suspeitos; nenhum conteúdo é reescrito ou recalculado. Cada achado abaixo traz o tratamento adotado. O porquê das decisões está em [`design-decisions.md`](design-decisions.md) (§5 e §6).

## Resumo

| Tabela | Registros | Observação |
|---|---|---|
| categorias | 9 | 3 sem produto |
| produtos | 65 | 62 active, 2 discontinued, 1 coming_soon |
| promoções | 25 | 4 ativas |
| clientes | 50 | 24 Campo Grande, 7 Dourados, 7 Três Lagoas, 6 Corumbá, 6 Ponta Porã |
| pedidos | 20 | 7 delivered, 4 cancelled, 4 confirmed, 4 pending, 1 shipped |
| itens de pedido | 22 | — |
| manual (PDF) | 8 páginas | texto extraído com `pypdf` |

**21 achados** · atenção: 8 · conflito: 8 · info: 3 · regra: 2

Severidade: **conflito** = o dado contradiz outro dado ou o manual; **regra** = o histórico viola uma regra do manual; **atenção** = estado que exige tratamento especial no atendimento; **info** = contexto.

## Achados

### Produtos

#### Descrição contradiz o nome ou as specs · _conflito_

- **Registros:** 135, 136, 137, 139, 140, 142, 144
- **O que foi visto:** A descrição fala de outra marca/modelo, de outra quantidade de cordas ou teclas, ou de outra madeira.
- **Tratamento:** Flag `description_conflict = true`. A descrição **não** é exposta ao cliente; o agente usa nome + specs + preço (BR-16).

| id | produto | divergência |
|---|---|---|
| 135 | Music Man Bass 1X Electric Bass | descrição cita Fender; o nome é Music Man; descrição cita Alder; specs.body = Ash |
| 136 | Ibanez Bass 2X Electric Bass | descrição cita Fender; o nome é Ibanez |
| 137 | Yamaha Bass 3X Electric Bass | descrição cita Ibanez; o nome é Yamaha; descrição diz 5 cordas; nome/specs dizem 4 |
| 139 | Bateria Acústica Yamaha Kit 1 Studio | descrição cita Pearl; o nome é Yamaha; descrição cita Mahogany/Poplar; specs.shells = Maple |
| 140 | Bateria Acústica Pearl Kit 2 Studio | descrição cita Tama; o nome é Pearl; descrição cita Birch; specs.shells = Maple |
| 142 | Teclado Sintetizador Korg Synth 1 Pro | descrição cita Roland; o nome é Korg; descrição diz 88 teclas; nome/specs dizem 61 |
| 144 | Teclado Sintetizador Roland Synth 3 Pro | descrição cita Yamaha; o nome é Roland |

#### Produtos descontinuados · _atenção_

- **Registros:** 113, 136
- **O que foi visto:** 113 Shelby SN-7C 7 Cordas Nylon Natural (estoque 0); 136 Ibanez Bass 2X Electric Bass (estoque 13)
- **Tratamento:** Nunca são oferecidos, mesmo com estoque; o agente informa que saíram do catálogo e sugere equivalentes (BR-01, BR-02; manual §7.3).

#### Ativos sem estoque · _atenção_

- **Registros:** 96
- **O que foi visto:** 96 Giannini GF-3D Dreadnought Sunburst
- **Tratamento:** Indisponível no momento + até 3 alternativas ofertáveis (BR-01, BR-02).

#### Lançamentos (`coming_soon`) · _conflito_

- **Registros:** 130
- **O que foi visto:** 130 Kala KA-ABP-CTG Barítono Cedar (estoque 2); pedidos que já o incluem: 3 (delivered), 14 (pending). Um produto ainda não lançado não deveria ter pedidos.
- **Tratamento:** Apresentado como lançamento em breve, sem venda e sem prazo prometido (BR-01). Os pedidos existentes continuam consultáveis normalmente.

#### Specs com esquemas diferentes por tipo de instrumento · _info_

- **O que foi visto:** O JSON de `specs` não tem um esquema único: violões/ukuleles, guitarras/baixos, baterias e teclados usam chaves diferentes.
- **Tratamento:** Specs são parseadas como dicionário e ganham o campo `specs_schema`; o resumo exibido ao cliente é montado por esquema.

| esquema | categorias | chaves | produtos |
|---|---|---|---|
| acustico | Ukuleles, Violões | back_sides, color, electronics, neck, scale, strings, top | 50 |
| bateria | Baterias e Percussão | color, hardware, pieces, shells | 3 |
| eletrico | Baixos, Guitarras | body, color, neck, strings | 9 |
| teclado | Teclados e Pianos | color, keys, polyphony, type | 3 |

#### Lote de produtos com outra origem · _info_

- **Registros:** 135, 136, 137, 138, 139, 140, 141, 142, 143, 144
- **O que foi visto:** 10 produtos têm o JSON de specs serializado em outro formato (`": "` com espaço). 7 dos 7 produtos com descrição conflitante estão nesse lote, cujos nomes também são genéricos ("Bass 1X", "Kit 1 Studio", "Synth 1 Pro").
- **Tratamento:** Nenhuma correção; reforça a decisão de não confiar na descrição desses itens.

### Categorias

#### Categorias sem nenhum produto · _atenção_

- **Registros:** 6, 7, 8
- **O que foi visto:** 6 Instrumentos de Sopro (Madeiras); 7 Instrumentos de Sopro (Metais); 8 Cordas Orquestrais. O manual (§1) diz que a loja vende instrumentos de sopro e cordas orquestrais.
- **Tratamento:** Vale o sistema: o agente nunca afirma ter itens dessas categorias e diz que não há no momento (BR-13, DD-§6).

### Promoções

#### Só 4 de 25 promoções estão ativas · _info_

- **Registros:** 12, 14, 19, 24
- **O que foi visto:** Nenhuma promoção tem data de início/fim; `is_active` é o único sinal.
- **Tratamento:** Só promoções com `is_active = 1` existem para o agente; as inativas nem chegam ao LLM (BR-03).

| promoção | produto | desconto | preço | preço final |
|---|---|---|---|---|
| 12 | 127 Kalani KAL-700T Tenor Natural | 10% | R$ 359,00 | R$ 323,10 |
| 14 | 121 Ohana CK-20 Concert Natural | 20% | R$ 549,00 | R$ 439,20 |
| 19 | 90 Crafter HT-100 Folk Aço Natural | 18% | R$ 2.399,00 | R$ 1.967,18 |
| 24 | 94 Taylor 110e Dreadnought Natural | 8% | R$ 5.999,00 | R$ 5.519,08 |

#### Produtos com mais de uma promoção · _atenção_

- **Registros:** 93, 96, 99
- **O que foi visto:** produto 93: #6 15%, #23 10%; produto 96: #8 18%, #22 40%; produto 99: #9 8%, #25 14%. Todas inativas.
- **Tratamento:** Se houver mais de uma ativa, vale o maior desconto (BR-03).

### Pedidos

#### Total do pedido diferente da soma dos itens · _conflito_

- **Registros:** 3, 20
- **O que foi visto:** A soma usa o preço **atual** do catálogo (não há preço histórico por item), então é um indício, não uma prova.
- **Tratamento:** Vale o `total_brl` registrado: é o que o cliente pagou (BR-16).

| pedido | data | status | pagamento | total | soma dos itens |
|---|---|---|---|---|---|
| 3 | 20/11/2025 | delivered | credit_12x | R$ 3.450,00 | R$ 3.498,00 |
| 20 | 08/03/2026 | cancelled | credit_3x | R$ 1.400,00 | R$ 1.488,00 |

#### Pagamento PIX sem o desconto de 5% · _regra_

- **Registros:** 1, 5, 10, 13, 17
- **O que foi visto:** O total é igual ao preço de tabela dos itens, sem os 5% do PIX (manual §3), e nenhum item tem promoção ativa que justifique não aplicar o desconto.
- **Tratamento:** Histórico não é recalculado. Para compras novas, o agente aplica o PIX corretamente (BR-04).

| pedido | data | status | pagamento | total | com PIX seria |
|---|---|---|---|---|---|
| 1 | 15/10/2025 | delivered | pix | R$ 11.499,00 | R$ 10.924,05 |
| 5 | 05/01/2026 | delivered | pix | R$ 4.599,00 | R$ 4.369,05 |
| 10 | 25/02/2026 | confirmed | pix | R$ 8.732,00 | R$ 8.295,40 |
| 13 | 12/03/2026 | pending | pix | R$ 1.899,00 | R$ 1.804,05 |
| 17 | 28/01/2026 | cancelled | pix | R$ 689,00 | R$ 654,55 |

#### Parcela abaixo do mínimo do manual §3.1 · _regra_

- **Registros:** 9, 16, 18
- **O que foi visto:** Faixas: até 3x, R$ 50,00; 4–6x, R$ 80,00; 7–12x, R$ 100,00.
- **Tratamento:** Histórico não é recalculado. Para compras novas, a tool só oferece o parcelamento permitido (BR-05).

| pedido | data | status | pagamento | total | parcela |
|---|---|---|---|---|---|
| 9 | 20/02/2026 | confirmed | credit_6x | R$ 349,90 | 6x de R$ 58,32 |
| 16 | 22/03/2026 | pending | credit_6x | R$ 429,90 | 6x de R$ 71,65 |
| 18 | 08/02/2026 | cancelled | credit_12x | R$ 459,90 | 12x de R$ 38,32 |

#### Pedidos atrasados em 02/11/2026 · _atenção_

- **Registros:** 8
- **O que foi visto:** Status `shipped`/`confirmed` com previsão de entrega já vencida.
- **Tratamento:** A tool marca `is_late` e recomenda passagem para humano (BR-09).

| pedido | data | status | pagamento | total | previsão |
|---|---|---|---|---|---|
| 8 | 15/02/2026 | shipped | debit | R$ 349,90 | 28/02/2026 |

#### Pedidos parados em 02/11/2026 · _atenção_

- **Registros:** 9, 10, 11, 12, 13, 14, 15, 16
- **O que foi visto:** Status `pending`/`confirmed` há mais de 7 dias sem envio.
- **Tratamento:** A tool marca `is_stale` e recomenda passagem para humano (BR-09).

| pedido | data | status | pagamento | total | dias parado |
|---|---|---|---|---|---|
| 9 | 20/02/2026 | confirmed | credit_6x | R$ 349,90 | 255 |
| 10 | 25/02/2026 | confirmed | pix | R$ 8.732,00 | 250 |
| 11 | 05/03/2026 | confirmed | boleto | R$ 359,00 | 242 |
| 12 | 10/03/2026 | confirmed | credit_3x | R$ 599,00 | 237 |
| 13 | 12/03/2026 | pending | pix | R$ 1.899,00 | 235 |
| 14 | 15/03/2026 | pending | debit | R$ 1.749,00 | 232 |
| 15 | 18/03/2026 | pending | credit_12x | R$ 8.348,00 | 229 |
| 16 | 22/03/2026 | pending | credit_6x | R$ 429,90 | 225 |

### Clientes

#### E-mail compartilhado por mais de um cliente · _conflito_

- **Registros:** 11, 43
- **O que foi visto:** `bruno.martins@jmail.com`: clientes 11, 43
- **Tratamento:** O contato sozinho não identifica o cliente: `get_order` exige número do pedido + contato, e `list_my_orders` busca só por telefone (BR-08).

### Manual de políticas (PDF)

#### Dois números apresentados como WhatsApp · _conflito_

- **O que foi visto:** Números no manual: (67) 3321-4500, (67) 3341-4444. A §1.2 traz "Telefone / WhatsApp" e a §7 traz outro número como o canal de WhatsApp.
- **Tratamento:** WhatsApp = (67) 3321-4500 (seção específica do canal); telefone = (67) 3341-4444 (BR-12, DD-§6).

#### E-mail com acento no domínio · _conflito_

- **O que foi visto:** E-mails no manual: `contato@emporiodamusica.com.br`, `contato@empóriodamusica.com.br`.
- **Tratamento:** Usado `contato@emporiodamusica.com.br` (capa); o acento é tratado como erro de digitação (BR-12, DD-§6).

#### Parcela mínima contraditória entre §3 e §3.1 · _conflito_

- **O que foi visto:** A tabela da §3 diz "parcela mínima de R$ 100,00" para todo o crédito; a §3.1 define faixas. Mínimos citados: R$ 50,00, R$ 80,00, R$ 100,00.
- **Tratamento:** Vale a §3.1 (mais específica): 1–3x R$ 50,00 · 4–6x R$ 80,00 · 7–12x R$ 100,00 (BR-05, DD-§6).

#### Tamanho do catálogo · _conflito_

- **O que foi visto:** O manual (§1) fala em mais de 300 instrumentos; o sistema tem 65 produtos.
- **Tratamento:** Vale o sistema; o agente nunca afirma ter o que não está na base.

#### Frete de exatamente R$ 500,00 não está coberto · _atenção_

- **O que foi visto:** §5.1: grátis "acima de R$ 500,00" e R$ 35,00 "abaixo de R$ 500,00".
- **Tratamento:** Até R$ 500,00 inclusive paga R$ 35,00; grátis só acima (BR-07).

#### Black Friday prevista em novembro × promoções do sistema · _atenção_

- **O que foi visto:** O manual (§6.1) promete Black Friday em novembro com 15% a 30% "em todo o catálogo"; o sistema tem só 4 promoções ativas, e todas as de Black Friday estão inativas.
- **Tratamento:** Vale o sistema: só promoção com `is_active = 1` (manual §7.3, DD-§6).

## Verificações sem divergência

- Produtos: Nome × specs (nylon/aço, elétrico, cor)
- Pedidos: Integridade (itens, rastreio no formato do manual §5.3, datas, motivo de cancelamento)

## Camada processada (`data/processed`)

Um JSON por tabela (`ensure_ascii=False`, datas em ISO 8601). Campos acrescentados:

- `products`: `name_normalized`, `brand`, `category_name`, `specs` (objeto), `specs_schema`, `description_conflict`, `description_conflict_reasons`, `search_text`.
- `customers`: `first_name`, `phone_digits`, `email_normalized`.
- `orders`: `payment_type`, `installments`, `items_total_brl`, `total_mismatch`, `pix_without_discount`, `installment_below_minimum`.
- `order_items`: `unit_price_brl` e `line_total_brl` (preço **atual** do catálogo).
- `promotions`: `is_active` como booleano.
- `policy_pages.json`: texto de cada página do manual, com o espaçamento corrigido (o `pypdf` devolve uma palavra por linha).

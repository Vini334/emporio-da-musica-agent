# Decisões de design

Registro das decisões tomadas antes de escrever código. Elas saíram de uma sessão de
planejamento em formato de "árvore de decisões": cada rodada só tratou das decisões cujos
pré-requisitos já estavam resolvidos. Cada item traz a escolha e o porquê.

## 1. Escopo e posicionamento

| Decisão | Escolha | Por quê |
|---|---|---|
| Quem usa o agente | O **cliente final**, direto (o "assistente virtual" do WhatsApp citado no manual, §7), com passagem para um humano | Todos os cenários do enunciado são falas de cliente. "Auxiliar a equipe" foi interpretado como absorver as perguntas recorrentes e escalar o resto. |
| Escopo | Todos os requisitos obrigatórios, mais avaliação automatizada e testes. Streamlit e LLM como juiz só se sobrar tempo | A avaliação é o que mais diferencia um protótipo de LLM confiável. |
| Interface | CLI primeiro; núcleo do agente desacoplado da interface | O enunciado foca no agente funcionando. |

## 2. LLM e framework

| Decisão | Escolha | Por quê |
|---|---|---|
| Provedor | **Agnóstico** via `init_chat_model` (`LLM_MODEL=<provider>:<model>`). Padrão: **Gemini Flash (free tier)** | Quem avalia consegue rodar sem custo, e trocar de provedor é só mudar uma variável. |
| Framework | **LangGraph** (grafo agente ↔ tools) | Padrão de mercado; troca de provedor por config; checkpointer pronto para memória; um grafo explícito facilita adicionar nós de verificação ou de passagem para humano. |
| Memória | Persistida em **SQLite** por `session_id` (`SqliteSaver`) | No WhatsApp a conversa continua; memória por cliente identificado ficou de fora por envolver LGPD. |

## 3. Acesso a dados (CSVs)

**Tools determinísticas e tipadas**, sem text-to-SQL. O LLM só escolhe a tool e os argumentos;
as regras de negócio ficam em Python, onde são testáveis. Isso torna a LGPD uma garantia do código
(o modelo não tem como consultar a tabela de clientes), torna auditável cada número que o agente
fala e impede que o LLM "aplique" uma promoção inativa ou acumule o PIX com uma promoção. A lista
de tools e os contratos estão em [`SPEC.md` §6](SPEC.md#6-contratos-das-tools).

Text-to-SQL ficou registrado como evolução, para perguntas abertas sobre o catálogo.

## 4. Políticas (PDF): RAG exposto como tool

- O manual é quebrado em trechos **por seção/subseção** (4.1, 4.2…), com o título no metadado, porque o documento já é estruturado assim.
- Embeddings **locais e multilíngues** via `fastembed` (ONNX, sem PyTorch), guardadas no **Chroma** persistente. Não exige uma segunda chave de API nem depende do provedor do LLM.
- Fica exposto como a tool `search_policies(question)`: o agente decide **quando** consultar.
- Para 8 páginas, colocar o manual inteiro no prompt também funcionaria. O RAG foi escolhido para escalar para manuais maiores e para deixar explícita a decisão de quando consultar política e quando consultar dados.
- **Busca híbrida (decidida na fase 3):** só com embeddings, o MiniLM multilíngue acertou a seção esperada no top-3 em 14 de 20 perguntas de teste. Errou justamente o vocabulário próprio da loja: "parcelar em quantas vezes" (não trouxe a §3.1), "boquilha" (§4.4), "trocar a cor" (§4.3), "boleto" (§3), "frete para Dourados" e "chegou quebrado" (§5.2). Somando um BM25 com radical de 5 letras ("parcelar" ≈ "Parcelamento") e fundindo os dois rankings por RRF, foram **20 de 20**. Com 25 trechos, o BM25 em memória não custa nada. Alternativas descartadas: modelo maior (mpnet, ~1 GB de download para quem avalia) e palavras-chave escritas à mão por seção (manutenção manual a cada versão do manual).
- **Extração em modo layout do `pypdf`:** o modo padrão devolve uma palavra por linha, o que apaga a diferença entre cabeçalho ("4.1 …" na coluna 0) e item numerado de lista (indentado). No modo layout essa diferença sobrevive, e as tabelas saem com colunas separadas por espaços largos, que viram `a | b | c`.

## 5. Tratamento de dados

O **bruto fica intocado** (`data/raw`). O script `python -m emporio.data.prepare` gera uma
**camada padronizada** (`data/processed`, um JSON por tabela) e o
[`data_quality_report.md`](data_quality_report.md).

- **Padronização técnica (feita):** tipos, enums de status, parse do JSON de specs, nome normalizado para busca (sem acento, minúsculo) e nomes de arquivo sem espaço e acento.
- **Correção de conteúdo (não feita):** nada é reescrito nem recalculado. O registro é marcado e uma regra de negócio é aplicada:
  - Descrição que contradiz o nome ou as specs (produtos 135–144): **não é mostrada**. O agente usa só nome + specs + preço.
  - Totais de pedido divergentes (pedidos 3 e 20): vale o `total_brl` registrado (é o que o cliente pagou). A divergência vai para o relatório.
  - `discontinued` com estoque (136): não é oferecido, conforme o manual §7.3.
  - `coming_soon` (130): apresentado como "lançamento em breve", sem venda e sem prazo prometido, mesmo havendo pedidos antigos dele.

## 6. Suposições sobre ambiguidades do manual

| Ambiguidade | Suposição adotada |
|---|---|
| Parcelamento: a tabela da §3 diz "mínimo R$100", a §3.1 tem faixas | Vale a **§3.1** (mais específica): até 3x com parcela mínima de R$50; 4–6x com R$80; 7–12x com R$100 |
| Dois WhatsApps: (67) 3341-4444 (§1.2) e (67) 3321-4500 (§7) | WhatsApp = **(67) 3321-4500** (seção específica do canal); telefone = (67) 3341-4444 |
| E-mail com acento no domínio (§1.2) | Usar `contato@emporiodamusica.com.br` (da capa); o acento é tratado como erro de digitação |
| O arrependimento só vale para compra online, e os pedidos não têm canal | Todo pedido do sistema é tratado como **online** (todos têm entrega e rastreio) |
| Os pedidos não têm data real de entrega | Usa `estimated_delivery` como data de recebimento, avisando o cliente quando a conclusão depende disso |
| Frete fora da região metropolitana não tem tabela | Calcula só a região metropolitana (grátis **acima** de R$500, senão R$35). Fora dela, informa as modalidades e prazos e não inventa valor; item de grande porte vai para cotação com um humano |
| "Mais de 300 instrumentos", sopros e cordas orquestrais | Vale o **sistema** (65 produtos; categorias 6–8 vazias). O agente nunca afirma ter o que não está na base |
| Black Friday em novembro (§6.1) e promoções inativas no CSV | Vale o **sistema** (§7.3): só é aplicada promoção com `is_active=1` |
| Frete especial de itens de grande porte aparece só na §5.2 (outras cidades) | Vale **em qualquer cidade**, inclusive Campo Grande: na dúvida, é melhor um humano cotar do que prometer frete grátis por motoboy para uma bateria acústica |
| "Pedidos acima de R$ 500" para frete grátis | Compara o preço com a promoção aplicada, antes do desconto do PIX (o meio de pagamento ainda não está definido na conversa) |
| Arredondamento de parcelas e descontos | Centavos com arredondamento "meio para cima", como em recibos (349,90 em 4x = R$ 87,48) |
| Orçamento ("violões até R$ 1000") | O teto compara o **menor** preço possível (PIX ou promoção), e a lista mostra primeiro os itens mais próximos do orçamento; o total encontrado vai junto para o agente oferecer refinar |
| Telefone informado pelo cliente | Só os dígitos contam; `+55` é opcional e o DDD é obrigatório (sem DDD não verifica, para não confundir clientes) |

## 7. Data, hora e horário de funcionamento

A data e a hora atuais em **`America/Campo_Grande` (UTC-4)**, e se a loja está aberta (incluindo
feriados nacionais, de MS e de Campo Grande, via a biblioteca `holidays`), são injetadas no prompt
a cada turno. O relógio pode ser fixado por `EMPORIO_NOW`, para que exemplos e avaliações sejam
reproduzíveis.

## 8. Persona e guardrails

- **"Dó"**, assistente virtual da Empório. Tom informal mas profissional (§7.1), mensagens curtas no estilo WhatsApp, emoji pontual. Sempre se identifica como assistente virtual.
- **Fora do escopo**, em três níveis:
  1. Acessórios: redirecionamento educado, conforme o manual.
  2. Assunto alheio à loja: recusa simpática e volta ao tema.
  3. Prompt injection ou engenharia social: recusa.
- **A defesa real está nas tools**: o modelo não tem acesso à tabela de clientes nem a qualquer forma de "aplicar desconto". Um classificador de entrada separado ficou como evolução.

## 9. Agente e interface (fase 4)

- **`create_agent` do LangChain 1.x** (um grafo ReAct do LangGraph) com middlewares, em vez de um `StateGraph` escrito à mão: o laço agente ↔ tools é o padrão, e os pontos de extensão necessários já existem como middleware.
  - `dynamic_prompt`: o prompt é remontado a **cada chamada do modelo**, então o bloco do relógio (agora, loja aberta ou fechada, quando a equipe volta) nunca fica velho numa conversa longa.
  - `ModelRetryMiddleware`: 3 novas tentativas com backoff exponencial (5 s, 10 s, 20 s) para o rate limit do free tier do Gemini. Se todas falharem, o cliente recebe uma mensagem amigável com o WhatsApp da loja, e não um stack trace.
  - `ToolCallLimitMiddleware`: no máximo 8 chamadas de tool por turno, com o modelo encerrando a resposta (em vez de um erro) se passar disso.
- **Modelo:** `gemini-flash-latest`, confirmado listando os modelos com a chave em 02/11/2026. É um alias que acompanha o Flash estável mais recente; para fixar uma versão, basta trocar o `LLM_MODEL`. Temperatura 0,2.
- **`session_id` na passagem para humano:** a tool lê o `thread_id` do `RunnableConfig` injetado pelo LangChain (o argumento não aparece no schema que o LLM vê). O modelo não tem como escolher nem forjar a sessão.
- **Protocolo `EMP-AAAAMMDD-NNNN`:** sequência por dia, calculada a partir do próprio `handoffs.jsonl` (fonte única, sem contador separado), com um lock para escritas concorrentes no mesmo processo.
- **`Assistant.send()`** devolve o texto e as chamadas de tool do turno (nome, argumentos, resultado). A CLI, o transcript e a avaliação (fase 6) usam a mesma interface, sem depender do formato interno das mensagens do LangGraph.
- **CLI por stdin:** sem terminal interativo, cada linha é uma mensagem. Os exemplos saem de um comando reproduzível (`printf ... | emporio-chat --now ... --transcript ...`), não de uma sessão digitada à mão.

## 10. Qualidade

- Testes unitários das tools e da preparação de dados (`pytest`).
- Suíte de avaliação com cerca de 25 casos (YAML): checagens determinísticas (tool esperada, números que devem ou não aparecer). LLM como juiz para o tom, se houver tempo.

**Avaliação ponta a ponta (decidida na fase 6):**

- **33 casos** em `eval/cases.yaml`, cobrindo as 10 categorias da SPEC (§12). Cada caso cita a regra (BR-nn) ou a armadilha de dados que testa, e os valores esperados saem das tools determinísticas, não de uma redação "ideal".
- **Checagens por turno, não só no final.** A expectativa pode estar em qualquer turno; a do caso vale para o último. Sem isso não daria para testar a LGPD em conversa: no turno 1 de "cadê meu pedido 13?" o agente **não pode** chamar `get_order`; no turno 2, com o telefone, tem que chamar.
- **Âncoras robustas à redação:** o texto é comparado sem acento, sem maiúsculas e sem negrito; números (R$, datas, %) são as âncoras principais porque vêm das tools; `must_contain_any` aceita sinônimos ("esgotado", "sem estoque", "indisponível"); `must_not_contain` pega o erro típico (PIX acumulado com promoção, a promoção inativa aplicada, dado de outro cliente). Checar argumentos de tool (`tool_args`) confirma que o pedido consultado é o certo.
- **Erro de infraestrutura ≠ falha do agente.** Se o modelo falhar depois das novas tentativas (rate limit do free tier), o caso sai como "erro" e não conta como falha de comportamento; o relatório mostra a taxa com e sem esses casos.
- **Isolamento:** cada caso roda numa conversa nova (checkpointer em memória), com o relógio fixado por caso, e a fila de passagem para humano vai para um arquivo temporário. A avaliação não suja `storage/`.
- **Código de saída 1** se algum caso falhar ou der erro, para poder virar um gate no CI.
- **Calibração:** a primeira rodada deu 29/33. Três falhas eram da suíte (sinônimo faltando, um trecho proibido que casava com a negação, "temos violino" dentro de "não temos violinos", e um caso de boquilha que não faz sentido numa loja sem sopros) e foram corrigidas nos casos. A quarta era do agente: na devolução fora do prazo, ele não dizia qual caminho ainda valia (BR-10). Isso foi corrigido no **prompt** (situação especial "troca ou devolução fora do prazo"), não afrouxando o caso. A segunda rodada completa deu 33/33.
- **LLM como juiz ficou de fora** desta fase: as checagens determinísticas cobrem o que é regra de negócio; tom e cordialidade ficaram para a fase 7.

**LLM como juiz (decidido na fase 7):**

- **Só forma, nunca conteúdo.** O que é regra de negócio já tem checagem determinística; pedir a um LLM para julgar preço ou prazo traria ruído e duplicaria a fonte de verdade. A rubrica do juiz manda ignorar a correção dos dados e não penalizar as recusas previstas (desconto, dados de terceiros, fora do escopo), só a simpatia delas.
- **Quatro critérios, de 1 a 5, tirados do próprio prompt** (persona e tom da §9): tom, cordialidade, concisão (estilo WhatsApp, sem jargão interno) e encaminhamento (próximo passo, uma pergunta por vez). Nota por critério, e não uma nota única, para a média apontar *o que* melhorar.
- **Saída estruturada** (`with_structured_output` com um modelo Pydantic, `ge=1, le=5`) e temperatura 0, com retry para o rate limit. Uma falha do juiz vira "sem nota" no caso e não interrompe a rodada.
- **O juiz vê os nomes das tools, não os JSONs.** Assim ele sabe que a resposta foi fundamentada sem ser tentado a conferir números.
- **Informativo, sem gate.** As notas não mudam o status nem o código de saída: com um juiz do mesmo modelo que respondeu, há viés de autopreferência (tende a dar nota alta), e um limiar fixo viraria um gate frágil. Mitigação: `--judge-model`/`JUDGE_MODEL` permite usar outro provedor; a rubrica pede severidade ("um 5 deve ser difícil de melhorar"). **Calibração:** uma resposta deliberadamente ruim (fria, com jargão interno e sem próximo passo) recebeu 1 nos quatro critérios, então o juiz discrimina; o teto alto nas conversas reais é, em parte, mérito do prompt e, em parte, esse viés.
- **Calibração da suíte nesta fase:** a rodada de teste do juiz pegou um falso negativo em `horario-feriado` ("a equipe está de folga" é correto, mas não continha "fechad"). Os sinônimos foram adicionados ao caso, com o mesmo critério da fase 6: corrigir a suíte quando a resposta está certa, e o prompt quando não está. A rodada completa seguinte deu 32/33, com um desvio real: em `politica-itens-sem-troca` a busca trouxe a §4.4 em 1º e o modelo generalizou que "todos podem ser trocados". A correção foi uma regra **geral** no prompt (usar todos os trechos, inclusive exceções e itens não elegíveis, e nunca generalizar contra eles), não um texto específico do caso; o caso passou 4 de 4 repetições e a rodada final deu 33/33, com média 4,9 do juiz.

**Interface Streamlit (decidida na fase 7):**

- **Mesma `build_agent()` e mesmo `Assistant.send()`** da CLI e da avaliação: a interface web não tem lógica própria de agente. O grafo compilado fica em `st.cache_resource` (um por servidor) e cada sessão do navegador tem o seu `session_id` na mesma memória SQLite.
- **Painel lateral de tools por turno** (o mais recente aberto), com a assinatura da chamada e o JSON do resultado: é o equivalente visual do `--verbose` e o que torna o roteamento (R4) visível numa demo.
- **Relógio simulável** na barra lateral, para demonstrar loja fechada, feriado e prazos sem mexer no `.env`. Ele grava `EMPORIO_NOW` no ambiente do processo, o que vale para todas as abas: aceitável numa demo local, documentado no código e na SPEC. Num deploy multiusuário, o relógio iria por sessão (por exemplo, no `RunnableConfig`).
- **Streamlit como dependência principal**, não extra opcional: um único `uv sync` deixa a CLI e a web prontas; o custo é o tamanho da instalação.
- **Testes com `AppTest`** e o mesmo modelo roteirizado dos testes do agente: turno com tool, painel, sugestões, nova conversa e relógio, sem LLM nem navegador.

## 11. Entrega

- 5 conversas em `examples/`, geradas pelo agente real com `EMPORIO_NOW` fixo:
  1. catálogo até R$1000;
  2. preço + promoção inativa;
  3. pedido com verificação de identidade e atraso;
  4. devolução fora do prazo;
  5. fora do escopo + injection.
- Código em inglês; prompts, README e exemplos em português.
- Commits pequenos por fase: scaffold → dados → tools → RAG → agente/CLI → exemplos/README → avaliação → Streamlit.

## 12. Ajustes depois da revisão final

Uma revisão da entrega completa (clone limpo + leitura dos exemplos contra os dados) encontrou
desvios que os 33 casos não pegavam. Cada um foi corrigido na causa, com teste ou caso de
avaliação novo:

| Achado | Correção |
|---|---|
| "Violões até R$ 1000" achava 13, mostrava 6 começando pelo GNF-3 (R$ 1.049, só cabe no PIX) e escondia os mais baratos; para o iniciante, o agente recomendava só entre os 6 mostrados | Com lista cortada, a tool devolve `price_range` e uma `note` pedindo que o agente diga quantas opções há. Itens que só cabem pagando no PIX vão para o fim. Novo `cheapest_first`, usado para iniciante e "o mais em conta". Caso `catalogo-iniciante-opcoes-mais-baratas` |
| Ao recusar um desconto, o agente sugeria "negociar condições especiais com a equipe" | O prompt manda apresentar o preço oficial e as condições existentes, já que a equipe segue a mesma tabela. O caso `injection-desconto-40` proíbe "negociar" |
| Numa resposta sobre parcelamento, o agente citou "parcela mínima de R$ 100,00" para tudo, a tabela contraditória do manual (§3) que o RAG trouxe | As faixas da §3.1 entram fixas no prompt, geradas de `rules/pricing.py`, com a ressalva de que o "R$ 100" vale só para 7x–12x |
| O agente disse que a loja vende "sopros", copiando o §1 do manual, mas a categoria está vazia | Bloco "Catálogo hoje" no prompt, gerado dos dados: categorias com instrumentos à venda e categorias vazias |
| Rodar o `prepare` em outro dia mudava o relatório versionado | A data de referência do relatório fica fixa em 02/11/2026 (`--as-of` recalcula) |

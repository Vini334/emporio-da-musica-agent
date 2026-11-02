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
as regras de negócio ficam em Python, onde são testáveis:

| Tool | Regras embutidas |
|---|---|
| `search_products` | exclui `discontinued`; marca `coming_soon`; promoção só se `is_active=1`; mostra preço original, % e preço final |
| `get_product` | idem; suprime a descrição conflitante; se sem estoque, sugere alternativas |
| `get_order` | exige número do pedido **e** e-mail ou telefone (LGPD); detecta atraso pela data atual |
| `list_my_orders` | só pelo telefone (o e-mail tem duplicata na base) |
| `check_return_eligibility` | prazos de 7, 30 e 90 dias, cada um contado a partir da data correta |
| `calculate_payment` | PIX com 5% e não cumulativo com promoção; parcela mínima por faixa |
| `search_policies` | RAG sobre o manual |
| `handoff_to_human` | grava a fila em `storage/handoffs.jsonl` e devolve um protocolo |

Text-to-SQL ficou registrado como evolução, para perguntas abertas sobre o catálogo.

## 4. Políticas (PDF): RAG exposto como tool

- O manual é quebrado em trechos **por seção/subseção** (4.1, 4.2…), com o título no metadado, porque o documento já é estruturado assim.
- Embeddings **locais e multilíngues** via `fastembed` (ONNX, sem PyTorch), guardadas no **Chroma** persistente. Não exige uma segunda chave de API nem depende do provedor do LLM.
- Fica exposto como a tool `search_policies(question)`: o agente decide **quando** consultar.
- Para 8 páginas, colocar o manual inteiro no prompt também funcionaria. O RAG foi escolhido para escalar para manuais maiores e para deixar explícita a decisão de quando consultar política e quando consultar dados.

## 5. Tratamento de dados

O **bruto fica intocado** (`data/raw`). O script `prepare_data.py` gera uma **camada padronizada**
(`data/processed`) e o **`data_quality_report.md`**.

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

## 9. Qualidade

- Testes unitários das tools e da preparação de dados (`pytest`).
- Suíte de avaliação com cerca de 25 casos (YAML): checagens determinísticas (tool esperada, números que devem ou não aparecer). LLM como juiz para o tom, se houver tempo.

## 10. Entrega

- 5 conversas em `examples/`, geradas pelo agente real com `EMPORIO_NOW` fixo:
  1. catálogo até R$1000;
  2. preço + promoção inativa;
  3. pedido com verificação de identidade e atraso;
  4. devolução fora do prazo;
  5. fora do escopo + injection.
- Código em inglês; prompts, README e exemplos em português.
- Commits pequenos por fase: scaffold → dados → tools → RAG → agente/CLI → exemplos/README → avaliação → Streamlit.

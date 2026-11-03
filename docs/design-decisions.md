# Design decisions

The decisions made before writing code. They came out of a planning session run as a "decision
tree": each round only covered decisions whose prerequisites were already settled. Each item
gives the choice and the reason for it.

## 1. Scope and positioning

| Decision | Choice | Why |
|---|---|---|
| Who uses the agent | The **end customer**, directly (the WhatsApp "virtual assistant" described in manual §7), with a hand-off to a human | Every scenario in the brief is a customer message. "Helping the team" was read as absorbing the recurring questions and escalating the rest. |
| Scope | Every mandatory requirement, plus automated evaluation and tests. Streamlit and LLM-as-judge only if time allowed | Evaluation is what most separates a reliable LLM prototype from a demo. |
| Interface | CLI first; the agent core is decoupled from the interface | The brief focuses on the agent working correctly. |

## 2. LLM and framework

| Decision | Choice | Why |
|---|---|---|
| Provider | **Provider-agnostic** via `init_chat_model` (`LLM_MODEL=<provider>:<model>`). Default: **Gemini Flash (free tier)** | Reviewers can run it at no cost, and switching provider is a single variable. |
| Framework | **LangGraph** (agent ↔ tools graph) | Industry standard; provider switch by config; a ready-made checkpointer for memory; an explicit graph makes it easy to add verification or hand-off nodes. |
| Memory | Persisted in **SQLite** per `session_id` (`SqliteSaver`) | On WhatsApp the conversation carries on; per-identified-customer memory was left out because it involves LGPD (Brazil's data protection law). |

## 3. Data access (CSVs)

**Deterministic, typed tools**, no text-to-SQL. The LLM only picks the tool and its arguments;
the business rules live in Python, where they can be tested. This makes LGPD compliance a
guarantee of the code (the model has no way to query the customers table), makes every number the
agent says auditable, and prevents the LLM from "applying" an inactive promotion or stacking the
PIX discount on top of a promotion. The tool list and contracts are in
[`SPEC.md` §6](SPEC.md#6-tool-contracts).

Text-to-SQL was recorded as a possible evolution, for open-ended catalog questions.

## 4. Policies (PDF): RAG exposed as a tool

- The manual is split into chunks **by section/subsection** (4.1, 4.2…), with the title in the metadata, because the document is already structured that way.
- **Local, multilingual** embeddings via `fastembed` (ONNX, no PyTorch), stored in a persistent **Chroma** collection. No second API key and no dependency on the LLM provider.
- Exposed as the `search_policies(question)` tool: the agent decides **when** to look things up.
- For 8 pages, putting the whole manual in the prompt would also work. RAG was chosen to scale to larger manuals and to make the "policy or data?" decision explicit.
- **Hybrid search (decided in phase 3):** with embeddings only, the multilingual MiniLM had the expected section in its top-3 for 14 of 20 test questions. It missed exactly the store's own vocabulary: "parcelar em quantas vezes" (installments; did not bring §3.1), "boquilha" (mouthpiece, §4.4), "trocar a cor" (exchange for another color, §4.3), "boleto" (§3), "frete para Dourados" (shipping to another city) and "chegou quebrado" (arrived broken, §5.2). Adding BM25 with a 5-letter stem ("parcelar" ≈ "Parcelamento") and fusing both rankings with Reciprocal Rank Fusion gave **20 of 20**. With 25 chunks, an in-memory BM25 costs nothing. Discarded alternatives: a larger model (mpnet, ~1 GB download for the reviewer) and hand-written keywords per section (manual upkeep for every new version of the manual).
- **`pypdf` in layout mode:** the default mode returns one word per line, which erases the difference between a heading ("4.1 …" at column 0) and a numbered list item (indented). Layout mode keeps that difference, and tables come out with columns separated by wide spaces, which are turned into `a | b | c`.

## 5. Data handling

The **raw data is never edited** (`data/raw`). The `python -m emporio.data.prepare` script builds
a **standardized layer** (`data/processed`, one JSON per table) and the
[`data_quality_report.md`](data_quality_report.md).

- **Technical standardization (done):** types, status enums, parsing of the specs JSON, a normalized name for search (no accents, lowercase) and file names without spaces or accents.
- **Content correction (not done):** nothing is rewritten or recalculated. The record is flagged and a business rule is applied:
  - A description that contradicts the name or specs (products 135–144) is **not shown**. The agent uses only name + specs + price.
  - Order totals that differ from their items (orders 3 and 20): the recorded `total_brl` prevails (it is what the customer paid). The mismatch goes to the report.
  - `discontinued` with stock (136): never offered, as manual §7.3 says.
  - `coming_soon` (130): presented as "coming soon", not for sale and with no promised date, even though there are past orders for it.

## 6. Assumptions about ambiguities in the manual

| Ambiguity | Assumption adopted |
|---|---|
| Installments: the §3 table says "minimum R$100", §3.1 has tiers | **§3.1** prevails (more specific): up to 3x with a R$50 minimum installment; 4–6x with R$80; 7–12x with R$100 |
| Two WhatsApp numbers: (67) 3341-4444 (§1.2) and (67) 3321-4500 (§7) | WhatsApp = **(67) 3321-4500** (the channel's own section); phone = (67) 3341-4444 |
| E-mail with an accent in the domain (§1.2) | Use `contato@emporiodamusica.com.br` (from the cover page); the accent is treated as a typo |
| The right of withdrawal (regret) applies only to online purchases, and orders have no channel | Every order in the system is treated as **online** (all of them have delivery and tracking) |
| Orders have no actual delivery date | `estimated_delivery` is used as the date of receipt, telling the customer when the conclusion depends on it |
| Shipping outside the metro area has no price table | Only the metro area is calculated (free **above** R$500, otherwise R$35). Elsewhere, the agent gives the shipping options and lead times and never invents a price; large items go to a human for a quote |
| "More than 300 instruments", wind and orchestral string instruments | The **system** prevails (65 products; categories 6–8 empty). The agent never claims to have what is not in the data |
| Black Friday in November (§6.1) and inactive promotions in the CSV | The **system** prevails (§7.3): only promotions with `is_active=1` are applied |
| Special shipping for large items appears only in §5.2 (other cities) | It applies **in any city**, Campo Grande included: when in doubt, it is better for a human to quote than to promise free motorcycle delivery of an acoustic drum kit |
| "Orders above R$ 500" for free shipping | Compares the price with the promotion applied, before the PIX discount (the payment method is not settled yet in the conversation) |
| Rounding of installments and discounts | Cents rounded "half up", as on receipts (349,90 in 4x = R$ 87,48) |
| Budget ("acoustic guitars up to R$ 1000") | The ceiling compares the **lowest** possible price (PIX or promotion). The list shows first the items closest to the budget that fit at full price; items that only fit when paying by PIX go last. The total found and the price range go to the agent so it can offer the rest |
| Phone given by the customer | Only the digits count; `+55` is optional and the area code (DDD) is mandatory (without it the check fails, so customers are not confused with each other) |

## 7. Date, time and opening hours

The current date and time in **`America/Campo_Grande` (UTC-4)**, and whether the store is open
(including national, Mato Grosso do Sul and Campo Grande holidays, via the `holidays` library),
are injected into the prompt on every turn. The clock can be pinned with `EMPORIO_NOW`, so
examples and evaluations are reproducible.

## 8. Persona and guardrails

- **"Dó"** (the note C, as in do-re-mi), the store's virtual assistant. Informal but professional tone (manual §7.1), short WhatsApp-style messages, occasional emoji. Always introduces itself as a virtual assistant.
- **Out of scope**, at three levels:
  1. Accessories: polite redirection, as the manual says.
  2. Topics unrelated to the store: a friendly refusal and back to the subject.
  3. Prompt injection or social engineering: refusal.
- **The real defense is in the tools:** the model has no access to the customers table and no way to "apply a discount". A separate input classifier was left as a possible evolution.

## 9. Agent and interface (phase 4)

- **LangChain 1.x `create_agent`** (a LangGraph ReAct graph) with middleware, instead of a hand-written `StateGraph`: the agent ↔ tools loop is the standard one, and the extension points needed already exist as middleware.
  - `dynamic_prompt`: the prompt is rebuilt on **every model call**, so the clock block (now, store open or closed, when the team is back) never goes stale in a long conversation.
  - `ModelRetryMiddleware`: 3 retries with exponential backoff (5 s, 10 s, 20 s) for the Gemini free-tier rate limit. If all fail, the customer gets a friendly message with the store's WhatsApp, not a stack trace.
  - `ToolCallLimitMiddleware`: at most 8 tool calls per turn, with the model wrapping up the reply (instead of an error) beyond that.
- **Model:** `gemini-flash-latest`, confirmed by listing the models with the API key on 2026-11-02. It is an alias that follows the latest stable Flash; to pin a version, change `LLM_MODEL`. Temperature 0.2.
- **`session_id` in the hand-off:** the tool reads the `thread_id` from the `RunnableConfig` injected by LangChain (the argument does not appear in the schema the LLM sees). The model cannot choose or forge the session.
- **Protocol `EMP-YYYYMMDD-NNNN`:** a per-day sequence computed from `handoffs.jsonl` itself (single source, no separate counter), with a lock for concurrent writes within the same process.
- **`Assistant.send()`** returns the turn's text and tool calls (name, arguments, result). The CLI, the transcript and the evaluation (phase 6) use the same interface, without depending on the internal format of LangGraph messages.
- **CLI over stdin:** without an interactive terminal, each line is a message. The examples come from a reproducible command (`printf ... | emporio-chat --now ... --transcript ...`), not from a hand-typed session.

## 10. Quality

- Unit tests for the tools and data preparation (`pytest`).
- An evaluation suite of about 25 cases (YAML): deterministic checks (expected tool, numbers that must or must not appear). LLM-as-judge for tone, if time allowed.

**End-to-end evaluation (decided in phase 6):**

- **33 cases** in `eval/cases.yaml` (34 after the final review, see §12), covering the SPEC's 10 categories (§12). Each case cites the rule (BR-nn) or the data trap it tests, and the expected values come from the deterministic tools, not from an "ideal" wording.
- **Per-turn checks, not just at the end.** An expectation can sit on any turn; the case-level one applies to the last turn. Without this, LGPD could not be tested in a conversation: on turn 1 of "where is my order 13?" the agent **must not** call `get_order`; on turn 2, with the phone number, it must.
- **Anchors robust to wording:** text is compared without accents, case or bold; numbers (R$, dates, %) are the main anchors because they come from the tools; `must_contain_any` accepts synonyms ("esgotado", "sem estoque", "indisponível", all meaning out of stock); `must_not_contain` catches the typical mistake (PIX stacked on a promotion, the inactive promotion applied, another customer's data). Checking tool arguments (`tool_args`) confirms the right order was looked up.
- **Infrastructure error ≠ agent failure.** If the model fails after the retries (free-tier rate limit), the case is reported as "error" and does not count as a behavior failure; the report shows the rate with and without those cases.
- **Isolation:** each case runs in a fresh conversation (in-memory checkpointer), with the clock pinned per case, and the hand-off queue goes to a temporary file. The evaluation leaves `storage/` untouched.
- **Exit code 1** if any case fails or errors, so it can become a CI gate.
- **Calibration:** the first run scored 29/33. Three failures were the suite's own fault (a missing synonym; a forbidden snippet that matched the negation, "temos violino" inside "não temos violinos"; and a mouthpiece case that makes no sense for a store without wind instruments) and were fixed in the cases. The fourth was the agent's: for a return past its deadline, it did not say which path was still open (BR-10). That was fixed in the **prompt** (special situation "exchange or return past the deadline"), not by loosening the case. The second full run scored 33/33.
- **LLM-as-judge was left out** of this phase: the deterministic checks cover the business rules; tone and courtesy were left for phase 7.

**LLM as judge (decided in phase 7):**

- **Form only, never content.** Business rules already have deterministic checks; asking an LLM to judge prices or deadlines would add noise and duplicate the source of truth. The judge's rubric says to ignore data correctness and not to penalize the expected refusals (discount, third-party data, out of scope), only how kind they are.
- **Four criteria, 1 to 5, taken from the prompt itself** (persona and tone, SPEC §9): tone, courtesy, concision (WhatsApp style, no internal jargon) and next step (a clear next step, one question at a time). One score per criterion rather than a single score, so the mean points at *what* to improve.
- **Structured output** (`with_structured_output` with a Pydantic model, `ge=1, le=5`) and temperature 0, with retries for the rate limit. A judge failure becomes "no score" for the case and does not stop the run.
- **The judge sees the tool names, not the JSON.** So it knows the reply was grounded without being tempted to check numbers.
- **Informative, not a gate.** The scores do not change the status or the exit code: with a judge from the same model that answered, there is self-preference bias (it tends to score high), and a fixed threshold would make a fragile gate. Mitigation: `--judge-model`/`JUDGE_MODEL` allows another provider; the rubric asks for severity ("a 5 should be genuinely hard to improve on"). **Calibration:** a deliberately bad reply (cold, with internal jargon and no next step) got 1 on all four criteria, so the judge does discriminate; the high ceiling on real conversations is partly the prompt's merit and partly that bias.
- **Suite calibration in this phase:** the judge's trial run caught a false negative in `horario-feriado` ("a equipe está de folga", the team is off, is correct but did not contain "fechad", closed). The synonyms were added to the case, with the same criterion as in phase 6: fix the suite when the reply is right, and the prompt when it is not. The next full run scored 32/33, with one real deviation: in `politica-itens-sem-troca` the search ranked §4.4 first and the model still generalized that "everything can be exchanged". The fix was a **general** rule in the prompt (use every excerpt, including exceptions and non-eligible items, and never generalize against them), not text specific to the case; the case passed 4 out of 4 repetitions and the final run scored 33/33, with a judge mean of 4.9.

**Streamlit interface (decided in phase 7):**

- **The same `build_agent()` and `Assistant.send()`** as the CLI and the evaluation: the web interface has no agent logic of its own. The compiled graph lives in `st.cache_resource` (one per server) and each browser session has its own `session_id` in the same SQLite memory.
- **Per-turn tool side panel** (the latest one expanded), with the call signature and the result JSON: the visual equivalent of `--verbose`, and what makes the routing (R4) visible in a demo.
- **Simulated clock** in the sidebar, to demo a closed store, a holiday and deadlines without touching `.env`. It writes `EMPORIO_NOW` to the process environment, which applies to every tab: acceptable for a local demo, documented in the code and in the SPEC. In a multi-user deployment, the clock would go per session (for example in the `RunnableConfig`).
- **Streamlit as a main dependency**, not an optional extra: a single `uv sync` gets both the CLI and the web app ready; the cost is a larger install.
- **Tests with `AppTest`** and the same scripted model used by the agent tests: a turn with a tool, the panel, suggestions, a new conversation and the clock, with no LLM and no browser.

## 11. Delivery

- 5 conversations in `examples/`, generated by the real agent with `EMPORIO_NOW` pinned:
  1. catalog up to R$1000;
  2. price + inactive promotion;
  3. order with identity check and delay;
  4. return past the deadline;
  5. out of scope + injection.
- **Language:** code, README, docs, generated reports and commit messages in English (the delivery requirement). The agent's prompt, tool outputs and the customer conversations stay in Brazilian Portuguese, the language of the store and its customers; each example opens with an English summary.
- Small commits per phase: scaffold → data → tools → RAG → agent/CLI → examples/README → evaluation → Streamlit.

## 12. Adjustments after the final review

A review of the complete delivery (fresh clone + reading the examples against the data) found
deviations that the 33 cases did not catch. Each one was fixed at the root, with a new test or
evaluation case:

| Finding | Fix |
|---|---|
| "Acoustic guitars up to R$ 1000" found 13, showed 6 starting with the GNF-3 (R$ 1.049, only fits when paying by PIX) and hid the cheapest ones; for a beginner, the agent recommended only among the 6 shown | A truncated list now carries `price_range` and a `note` asking the agent to say how many options exist. Items that only fit when paying by PIX go last. New `cheapest_first`, used for beginners and "the cheapest one". Case `catalogo-iniciante-opcoes-mais-baratas` |
| When declining a discount, the agent suggested "negotiating special conditions with the team" | The prompt says to present the official price and the existing conditions, since the team follows the same price list. Case `injection-desconto-40` forbids "negociar" (negotiate) |
| In a reply about installments, the agent quoted "parcela mínima de R$ 100,00" (minimum installment R$ 100) for everything: the contradictory §3 table that RAG retrieved | The §3.1 tiers are fixed in the prompt, generated from `rules/pricing.py`, with the caveat that the "R$ 100" applies only to 7x–12x |
| The agent said the store sells wind instruments, copying manual §1, but that category is empty | A "Catalog today" block in the prompt, generated from the data: categories with instruments for sale and empty categories |
| Running `prepare` on another day changed the committed report | The report's reference date is pinned to 2026-11-02 (`--as-of` recalculates) |
| Last full run (after the translation): 33/34. In `catalogo-categoria-vazia` the agent correctly said there are no violins, but from the new "Catálogo hoje" prompt block instead of a search, which the case required | Same criterion as before: the answer is right and grounded in data, so the suite changes. The case no longer requires a tool; re-checked offline against the saved reply, without re-running the model |

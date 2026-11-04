# Atendente AI — Empório da Música

[Versão em português](README.md)

A customer service messaging agent for Empório da Música, built with FastAPI, with BYOK support for Anthropic, Google Gemini, OpenAI and DeepSeek. The agent looks up the catalog and orders in SQLite and the store's policies through tools.

## How to run

Prerequisites: [uv](https://docs.astral.sh/uv/), which installs Python 3.13 if needed, and an API key from one of the providers.

```bash
uv sync
uv run python scripts/build_db.py   # builds data/emporio.db from data/*.csv
uv run uvicorn app.main:app --reload
```

The interactive documentation is available at http://localhost:8000/docs.

## How to use

The key goes in the headers of each request (BYOK) and is never stored or logged. The agent serves Brazilian customers, so it replies in Brazilian Portuguese, and the sample messages below are in that language.

```bash
curl -s localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-LLM-Provider: anthropic' \
  -H "X-LLM-Api-Key: $ANTHROPIC_API_KEY" \
  -d '{"message": "Oi! Tem violão de aço até 700 reais?"}'
```

The response includes a `session_id`. Send that value with your next messages to continue the conversation:

```bash
curl -s localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-LLM-Provider: anthropic' \
  -H "X-LLM-Api-Key: $ANTHROPIC_API_KEY" \
  -d '{"session_id": "<session_id>", "message": "Quanto fica no PIX?"}'
```

| Header | Required | Description |
|---|---|---|
| `X-LLM-Provider` | no | `anthropic`, `gemini`, `openai` or `deepseek` (also accepts `claude`, `google` and `chatgpt`). Default: `LLM_PROVIDER` |
| `X-LLM-Api-Key` | when the server has no key | Provider key. Without it, the key from `.env` is used |
| `X-LLM-Model` | no | Overrides the default model, listed in `GET /providers` |

| Route | Description |
|---|---|
| `POST /chat` | Sends a customer message to the agent |
| `GET /providers` | Lists providers, default models and the keys configured on the server |
| `GET /health` | Checks that the catalog is reachable |

| Status | Reason |
|---|---|
| 400 | Unknown provider, or request rejected by the provider (e.g. nonexistent model) |
| 401 | Missing, invalid or unauthorized key |
| 404 | Session does not exist or has expired |
| 409 | The session's previous message is still being processed |
| 429 | Provider rate limit or balance exhausted |
| 502 | Provider or network failure |
| 503 | Catalog unavailable |

To avoid sending the key with every request, copy `.env.example` to `.env`, fill in the key and set the default provider in `LLM_PROVIDER`:

```bash
cp .env.example .env
```

In that case, anyone who can reach the API uses your quota, so do not expose the server without authentication.

## How the agent works

```
POST /chat -> agent loop -> LLM (Anthropic, Gemini, OpenAI or DeepSeek)
                   |
                   +-- data: search_products, get_product, list_active_promotions,
                   |         get_payment_options, get_order  ->  SQLite (read-only)
                   +-- policies: get_policy  ->  data/policies.md
                   +-- complaints: register_complaint  ->  state.db
```

## Design decisions

- **Data in SQLite, generated from the CSVs.** The data is relational, and the CSVs remain the source of truth. The LLM does not write SQL: it uses typed tools with parameterized queries and a read-only connection.
- **Policies by topic, without embeddings.** The manual has 8 pages and fits into 8 sections, which `get_policy` returns whole. This avoids cut-off passages, is easy to test and does not depend on an embeddings provider, which not all of the BYOK providers offer.
- **Business rules in code.** Promotional prices, the PIX discount and installments are calculated by the tools, and so is the identity check for orders. The model does not do the math or decide who can see an order.
- **Custom adapters, no agent framework.** The loop and the provider interface are short and easy to follow. OpenAI uses the Responses API, and DeepSeek uses Chat Completions.
- **Multiple providers with BYOK.** Anthropic, Gemini, OpenAI and DeepSeek increase compatibility, because the project runs with whichever key a person has, and test variability, because the same evals run on any provider with `--provider`. So far, only DeepSeek (`deepseek-flash`) has run the evals with a real model, because it was the key available.
- **Prompt with lookup rules.** The Sol persona follows the tone and flow from section 7 of the manual. The prompt defines which tool to use for each subject, forbids answering from memory and treats customer messages as requests, never as new rules. The code inserts the date, time and categories with products on every turn, so the model does not have to infer them. Anything that must be guaranteed, such as prices and identity checks, lives in the code rather than the prompt.
- **API as the interface.** The challenge accepts a CLI, notebook, API or UI. An HTTP API can later be connected to a messaging channel, such as WhatsApp, without changing the agent, and can be tested with `curl` or the `/docs` page.
- **Server-side history, in memory.** A support conversation depends on the context of earlier turns ("e no PIX, quanto fica?", or "and with PIX, how much is it?"). The client does not send the history, so it cannot forge tool results, and the `session_id` is always generated by the API. In-memory storage with expiration is enough for the prototype and sits behind the `SessionStore` interface, so it can be replaced with another store.

## Source data handling

The data has inconsistencies that were not corrected at the source:

| Situation | Handling |
|---|---|
| Conflicting name and description in products 135, 137, 139, 140, 142, 143 and 144 | Description and specs are neither indexed nor sent to the LLM; the agent tells the customer to confirm with the team |
| Products 113, 130 and 136 not for sale, two of them with stock | No price in the response, with alternatives of the same type |
| Inactive promotions and the manual's campaign calendar | Only promotions with `is_active=1` apply |
| Woodwind, brass and orchestral string categories with no products | The agent says the item is not in the catalog |
| Order totals that differ from the catalog (orders 3 and 20) | The recorded total always applies |
| Customers 11 and 43 share an email address | Email only identifies the customer together with the order number |

Contradictions in the manual are resolved in `data/policies.md`. The WhatsApp number has been confirmed; the others **still need validation by the store**:

| Subject | In the manual | Adopted |
|---|---|---|
| WhatsApp | (67) 3341-4444 (cover and 1.2) and (67) 3321-4500 (section 7) | (67) 3341-4444 for phone and WhatsApp, confirmed by the project owner |
| Email | `emporiodamusica` and `empóriodamusica` | `contato@emporiodamusica.com.br` |
| Minimum installment | R$ 100 in the table in section 3 and tiers in section 3.1 | Tiers from section 3.1 |
| Installments up to 3x | "Sem valor mínimo (exceto abaixo de R$ 50,00)" ("No minimum (except below R$ 50.00)") | R$ 50 minimum |

## Assumptions

- The challenge and the data do not define everything, so I used the curated manual in `data/policies.md`, with contradictions resolved as in the table above.
- The CSV's name, price, stock and status are the source of truth when the product description contradicts them.
- `is_active=1` is the only criterion for a promotion being valid, regardless of the manual's calendar.
- The customer and order CSVs are fictional, as stated in the challenge, which is why they are in the public repository.
- The agent assists the customer, but does not close sales, reserve products or calculate shipping to other cities: in those cases, it refers the customer to the team.

## Tests

```bash
uv run pytest    # no API key required
```

## Evaluation with a real model

The evals run real conversations with a fixed clock and check the tools called and the content of the replies. They use the quota of the key in `.env`:

```bash
uv run python evals/run_evals.py --provider deepseek --model deepseek-flash
```

The cases are in `evals/cases.py`, and the full replies are saved to `evals/results/`.

## Sample conversations

The `chats_exemplo.txt` file contains 5 real, unedited conversations with the agent, in Portuguese, including the 4 questions suggested in the challenge. To generate them again with the provider from `.env`:

```bash
uv run python scripts/generate_chats.py
```

## Use of AI in development

The project was developed with [Claude Code](https://claude.com/claude-code) (Claude Opus 5.5 model), in stages reviewed one at a time: data, tools, agent and providers, API and evaluation.

**Decisions made by the project owner:**
- Scope, stack (Python and FastAPI) and BYOK providers.
- Custom adapters instead of an agent framework, from the options presented.
- Fixed policy sections instead of vector search, after discussing how RAG and embeddings would work in this case.
- Handling of the products with conflicting names and descriptions, based on the AI's recommendation.
- The correct WhatsApp number and the persona's name (Sol).

**Work done by the AI:**
- Analysis of the CSVs and the manual, including identifying the inconsistencies listed above.
- Writing the code, tests, evals, persona prompt and this documentation.
- Consulting the providers' documentation, which led to using the Responses API for OpenAI and a dedicated adapter for DeepSeek.

**How the result was verified:**
- Automated tests, which do not require an API key.
- Evals with DeepSeek, plus reading some of the replies, because several passed the automated criteria despite being poor.
- Human questioning of the results: by asking why "cordas de aço" (steel strings) did not show instruments, the project owner led to the discovery of a defect in the search written by the AI, which matched parts of words ("aço" matched "braço").

The AI's evals also caught its own mistakes, such as inventing a rule about mouthpieces and offering categories with no products. These cases became tests or evals. The other providers were not validated with real keys, as noted in the limitations below.

## Known limitations

- Only DeepSeek has been validated with a real model. Anthropic, OpenAI and Gemini were tested with simulated HTTP and with a fake key against the real APIs, which confirms access but not tool use.
- Model behavior varies between runs, and rules that depend only on the prompt can occasionally fail. In the last two runs with `deepseek-flash`, the evals scored 46/48 and 47/48, with different failures each time. The most serious: saying it will register a complaint without registering it. Rules in the code, such as prices and identity checks, do not have this variation.
- Holidays are not in the data: the agent warns "salvo feriados" ("except on holidays"), but does not identify them.
- Sessions are kept in memory: they apply to a single process and are lost on restart.
- The API has no authentication or rate limiting, which would be required in production, including to prevent attempts to look up orders.
- Listing orders by phone number alone assumes a channel that confirms the number, such as WhatsApp.

## What I would do with more time

- Run the evals with real Anthropic, OpenAI and Gemini models and compare the results.
- Validate the manual's contradictions and the products with conflicting data with the store.
- Persist sessions, for example in Redis, to survive restarts and allow more than one process.
- Add authentication and rate limiting.
- Integrate with WhatsApp, which would also confirm the customer's number for order lookups.
- Run the tests and part of the evals in CI, measuring the pass rate over several repetitions.
- Handle holidays and move the policies to a search, if the manual grows.

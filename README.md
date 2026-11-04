# Atendente AI — Empório da Música

Agente de atendimento por mensagens da Empório da Música, em FastAPI, com BYOK para Anthropic, Google Gemini, OpenAI e DeepSeek. O agente consulta o catálogo e os pedidos em SQLite e as políticas da loja por meio de tools.

## Como executar

```bash
uv sync
uv run python scripts/build_db.py   # gera data/emporio.db a partir de data/*.csv
uv run uvicorn app.main:app --reload
```

A documentação interativa fica em http://localhost:8000/docs.

## Como usar

A chave vai nos headers de cada requisição (BYOK), e nunca é salva nem registrada em logs.

```bash
curl -s localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-LLM-Provider: anthropic' \
  -H "X-LLM-Api-Key: $ANTHROPIC_API_KEY" \
  -d '{"message": "Oi! Tem violão de aço até 700 reais?"}'
```

A resposta traz um `session_id`. Envie esse valor nas próximas mensagens para continuar a conversa:

```bash
curl -s localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-LLM-Provider: anthropic' \
  -H "X-LLM-Api-Key: $ANTHROPIC_API_KEY" \
  -d '{"session_id": "<session_id>", "message": "Quanto fica no PIX?"}'
```

| Header | Obrigatório | Descrição |
|---|---|---|
| `X-LLM-Provider` | não | `anthropic`, `gemini`, `openai` ou `deepseek` (aceita `claude`, `google` e `chatgpt`). Padrão: `LLM_PROVIDER` |
| `X-LLM-Api-Key` | sem chave no servidor | Chave do provedor. Sem ela, usa a do `.env` |
| `X-LLM-Model` | não | Substitui o modelo padrão, listado em `GET /providers` |

| Rota | Descrição |
|---|---|
| `POST /chat` | Envia uma mensagem do cliente ao agente |
| `GET /providers` | Lista provedores, modelos padrão e chaves configuradas no servidor |
| `GET /health` | Verifica se o catálogo está acessível |

| Status | Motivo |
|---|---|
| 400 | Provedor desconhecido |
| 401 | Chave ausente, inválida ou sem permissão |
| 404 | Sessão inexistente ou expirada |
| 409 | A mensagem anterior da sessão ainda está em andamento |
| 429 | Limite ou saldo do provedor esgotado |
| 502 | Falha do provedor ou de rede |
| 503 | Catálogo indisponível |

Para usar chaves no servidor, copie `.env.example` para `.env`. Nesse caso, quem acessar a API consome a sua cota, então não exponha o servidor sem autenticação.

## Testes

```bash
uv run pytest    # não precisa de chave de API
```

## Limitações conhecidas

- As sessões ficam em memória: valem para um único processo e se perdem ao reiniciar.
- A API não tem autenticação nem limite de requisições, o que seria necessário em produção, inclusive para evitar tentativas de consulta a pedidos.
- Listar pedidos só com o telefone pressupõe um canal que confirme o número, como o WhatsApp.

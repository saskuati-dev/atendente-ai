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
| 400 | Provedor desconhecido, ou requisição recusada pelo provedor (ex.: modelo inexistente) |
| 401 | Chave ausente, inválida ou sem permissão |
| 404 | Sessão inexistente ou expirada |
| 409 | A mensagem anterior da sessão ainda está em andamento |
| 429 | Limite ou saldo do provedor esgotado |
| 502 | Falha do provedor ou de rede |
| 503 | Catálogo indisponível |

Para usar chaves no servidor, copie `.env.example` para `.env`. Nesse caso, quem acessar a API consome a sua cota, então não exponha o servidor sem autenticação.

## Como o agente funciona

```
POST /chat -> loop do agente -> LLM (Anthropic, Gemini, OpenAI ou DeepSeek)
                   |
                   +-- dados: search_products, get_product, list_active_promotions,
                   |          get_payment_options, get_order  ->  SQLite (somente leitura)
                   +-- políticas: get_policy  ->  data/policies.md
                   +-- reclamações: register_complaint  ->  state.db
```

## Decisões de projeto

- **Dados em SQLite, gerados dos CSVs.** Os dados são relacionais e os CSVs seguem como fonte de verdade. O LLM não escreve SQL: usa tools tipadas com consultas parametrizadas e conexão somente leitura.
- **Políticas por tópico, sem embeddings.** O manual tem 8 páginas e cabe em 8 seções, que `get_policy` devolve inteiras. Isso evita trechos cortados, é fácil de testar e não depende de um provedor de embeddings, que nem todos os provedores do BYOK oferecem.
- **Regras de negócio no código.** Preço promocional, desconto PIX e parcelas são calculados pelas tools, e a verificação de identidade dos pedidos também. O modelo não faz contas nem decide quem pode ver um pedido.
- **Adapters próprios, sem framework de agentes.** O loop e a interface com os provedores são curtos e visíveis. A OpenAI usa a API Responses, e o DeepSeek, Chat Completions.
- **Histórico no servidor.** O cliente não envia o histórico, para não poder forjar resultados de tools. O `session_id` é sempre gerado pela API.

## Tratamento dos dados de origem

Os dados têm inconsistências que não foram corrigidas na origem:

| Situação | Tratamento |
|---|---|
| Nome e descrição em conflito nos produtos 135, 137, 139, 140, 142, 143 e 144 | Descrição e specs não são indexados nem enviados ao LLM; o agente orienta a confirmar com a equipe |
| Produtos 113, 130 e 136 fora de venda, dois deles com estoque | Sem preço na resposta, com alternativas do mesmo tipo |
| Promoções inativas e calendário de campanhas do manual | Só valem as promoções com `is_active=1` |
| Categorias de sopros e cordas orquestrais sem produtos | O agente diz que o item não está no catálogo |
| Totais de pedidos diferentes do catálogo (pedidos 3 e 20) | Vale sempre o total registrado |
| Clientes 11 e 43 com o mesmo e-mail | E-mail só identifica o cliente junto com o número do pedido |

Contradições do manual, resolvidas em `data/policies.md`. O WhatsApp foi confirmado; as demais **ainda precisam de validação da loja**:

| Assunto | No manual | Adotado |
|---|---|---|
| WhatsApp | (67) 3341-4444 (capa e 1.2) e (67) 3321-4500 (seção 7) | (67) 3341-4444 para telefone e WhatsApp, confirmado pelo responsável |
| E-mail | `emporiodamusica` e `empóriodamusica` | `contato@emporiodamusica.com.br` |
| Parcela mínima | R$ 100 na tabela da seção 3 e faixas na seção 3.1 | Faixas da seção 3.1 |
| Parcelas até 3x | "Sem valor mínimo (exceto abaixo de R$ 50,00)" | Mínimo de R$ 50 |

## Testes

```bash
uv run pytest    # não precisa de chave de API
```

## Avaliação com modelo real

As evals rodam conversas reais, com o relógio fixo, e verificam as tools chamadas e o conteúdo das respostas. Consomem a cota da chave do `.env`:

```bash
uv run python evals/run_evals.py --provider deepseek --model deepseek-flash
```

Os casos estão em `evals/cases.py`, e as respostas completas ficam em `evals/results/`.

## Uso de IA no desenvolvimento

O projeto foi desenvolvido com o [Claude Code](https://claude.com/claude-code) (modelo Claude Opus 5.5), em etapas revisadas uma de cada vez: dados, tools, agente e provedores, API e avaliação.

**Decisões do responsável pelo projeto:**
- Escopo, stack (Python e FastAPI) e provedores do BYOK.
- Adapters próprios em vez de um framework de agentes, entre as opções apresentadas.
- Políticas por seções fixas em vez de busca vetorial, depois de discutir como RAG e embeddings funcionariam neste caso.
- Tratamento dos produtos com nome e descrição em conflito, com base na recomendação da IA.
- Número de WhatsApp correto e nome da persona (Sol).

**Trabalho da IA:**
- Análise dos CSVs e do manual, incluindo a identificação das inconsistências listadas acima.
- Escrita do código, dos testes, das evals, do prompt da persona e desta documentação.
- Consulta à documentação dos provedores, que levou a usar a API Responses na OpenAI e um adapter próprio para o DeepSeek.

**Como o resultado foi verificado:**
- Testes automatizados, que não precisam de chave de API.
- Evals com o DeepSeek, além da leitura de parte das respostas, porque várias passavam nos critérios automáticos mesmo estando ruins.
- Questionamento humano dos resultados: ao perguntar por que "cordas de aço" não mostrava instrumentos, o responsável levou à descoberta de um defeito na busca escrita pela IA, que comparava trechos de palavras ("aço" casava com "braço").

As evals da IA também encontraram erros dela mesma, como a invenção de uma regra sobre boquilhas e a oferta de categorias sem produtos. Esses casos viraram testes ou evals. Os demais provedores não foram validados com chaves reais, como indicado nas limitações abaixo.

## Limitações conhecidas

- Só o DeepSeek foi validado com um modelo real. Anthropic, OpenAI e Gemini foram testados com HTTP simulado e com chave falsa nas APIs reais, o que confirma o acesso, mas não o uso das tools.
- As sessões ficam em memória: valem para um único processo e se perdem ao reiniciar.
- A API não tem autenticação nem limite de requisições, o que seria necessário em produção, inclusive para evitar tentativas de consulta a pedidos.
- Listar pedidos só com o telefone pressupõe um canal que confirme o número, como o WhatsApp.

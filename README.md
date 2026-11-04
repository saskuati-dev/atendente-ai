# Atendente AI — Empório da Música

[English version](README.en.md)

Agente de atendimento por mensagens da Empório da Música, em FastAPI, com BYOK para Anthropic, Google Gemini, OpenAI e DeepSeek. O agente consulta o catálogo e os pedidos em SQLite e as políticas da loja por meio de tools.

## Como executar

Pré-requisitos: [uv](https://docs.astral.sh/uv/), que instala o Python 3.13 se necessário, e uma chave de API de um dos provedores.

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

Para não enviar a chave em cada requisição, copie `.env.example` para `.env`, preencha a chave e defina o provedor padrão em `LLM_PROVIDER`:

```bash
cp .env.example .env
```

Nesse caso, quem acessar a API consome a sua cota, então não exponha o servidor sem autenticação.

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
- **Vários provedores com BYOK.** Anthropic, Gemini, OpenAI e DeepSeek aumentam a compatibilidade, porque o projeto roda com a chave que a pessoa tiver, e a variabilidade dos testes, porque as mesmas evals rodam em qualquer provedor com `--provider`. Até agora, só o DeepSeek (`deepseek-flash`) rodou as evals com modelo real, por ser a chave disponível.
- **Prompt com regras de consulta.** A persona Sol segue o tom e o fluxo da seção 7 do manual. O prompt define qual tool usar para cada assunto e proíbe responder de memória, e trata as mensagens do cliente como pedidos, nunca como novas regras. A data, a hora e as categorias com produtos são inseridas pelo código a cada turno, para o modelo não precisar deduzi-las. O que precisa ser garantido, como preços e verificação de identidade, fica no código e não no prompt.
- **Interface em API.** O enunciado aceita CLI, notebook, API ou UI. Uma API HTTP pode ser ligada depois a um canal de mensagens, como o WhatsApp, sem mudar o agente, e pode ser testada com `curl` ou pela página `/docs`.
- **Histórico no servidor, em memória.** Uma conversa de atendimento depende do contexto dos turnos anteriores ("e no PIX, quanto fica?"). O cliente não envia o histórico, para não poder forjar resultados de tools, e o `session_id` é sempre gerado pela API. A memória com expiração basta para o protótipo e fica atrás da interface `SessionStore`, que permite trocá-la por outro armazenamento.

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

## Suposições adotadas

- O enunciado e os dados não definem tudo, então usei o manual curado em `data/policies.md`, com as contradições resolvidas como na tabela acima.
- Nome, preço, estoque e status do CSV valem como fonte quando a descrição do produto os contradiz.
- `is_active=1` é o único critério para uma promoção estar valendo, independentemente do calendário do manual.
- Os CSVs de clientes e pedidos são fictícios, como no enunciado, e por isso ficam no repositório público.
- O agente atende o cliente, mas não fecha vendas, não reserva produtos e não calcula frete para outras cidades: nesses casos, encaminha para a equipe.

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

## Exemplos de conversa

O arquivo `chats_exemplo.txt` traz 5 conversas reais com o agente, sem edição, incluindo as 4 perguntas sugeridas no enunciado. Para gerar de novo, com o provedor do `.env`:

```bash
uv run python scripts/generate_chats.py
```

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
- O comportamento do modelo varia entre execuções, e as regras que dependem só do prompt podem falhar de vez em quando. Nas duas últimas rodadas com `deepseek-flash`, as evals deram 46/48 e 47/48, com falhas diferentes a cada vez. A mais séria: dizer que vai registrar uma reclamação sem registrar. Regras no código, como preços e verificação de identidade, não têm essa variação.
- Feriados não constam nos dados: o agente avisa "salvo feriados", mas não os identifica.
- As sessões ficam em memória: valem para um único processo e se perdem ao reiniciar.
- A API não tem autenticação nem limite de requisições, o que seria necessário em produção, inclusive para evitar tentativas de consulta a pedidos.
- Listar pedidos só com o telefone pressupõe um canal que confirme o número, como o WhatsApp.

## O que eu faria com mais tempo

- Rodar as evals com modelos reais de Anthropic, OpenAI e Gemini e comparar os resultados.
- Validar com a loja as contradições do manual e os produtos com dados em conflito.
- Persistir as sessões, por exemplo em Redis, para sobreviver a reinícios e permitir mais de um processo.
- Adicionar autenticação e limite de requisições.
- Integrar com o WhatsApp, o que também confirmaria o número do cliente nas consultas de pedido.
- Rodar os testes e uma parte das evals em CI, medindo a taxa de acerto em várias repetições.
- Tratar feriados e passar as políticas para uma busca, caso o manual cresça.

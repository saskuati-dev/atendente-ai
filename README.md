# Atendente AI — Empório da Música

Agente de atendimento por mensagens da Empório da Música. Projeto em construção.

## Preparar os dados

```bash
uv sync
uv run python scripts/build_db.py   # gera data/emporio.db a partir de data/*.csv
uv run pytest
```

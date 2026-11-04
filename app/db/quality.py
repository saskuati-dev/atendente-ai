"""Problemas conhecidos de qualidade nos dados de origem, que não são corrigidos nos CSVs."""

# A descrição e os specs destes produtos contradizem o nome (marca, modelo ou tipo de
# instrumento). Exemplo: o 135 se chama "Music Man", mas é descrito como "Fender Jazz Bass".
# Nome, preço, estoque e status continuam valendo; descrição e specs não são indexados na
# busca nem enviados ao LLM, que não pode afirmar o que não vê.
PRODUCTS_WITH_CONFLICTING_DATA = frozenset({135, 137, 139, 140, 142, 143, 144})

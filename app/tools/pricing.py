"""Regras de preço do manual (seções 3, 3.1 e 6.2). Valores em centavos."""

from dataclasses import dataclass

PIX_DISCOUNT_PERCENT = 5
COMBINED_PAYMENT_THRESHOLD_CENTS = 200_000
MAX_INSTALLMENTS = 12

# (até N parcelas, parcela mínima em centavos)
INSTALLMENT_TIERS = ((3, 5_000), (6, 8_000), (MAX_INSTALLMENTS, 10_000))


@dataclass(frozen=True)
class Installment:
    count: int
    value_cents: int


def format_brl(cents: int) -> str:
    """Formata centavos em reais: 123456 -> "R$ 1.234,56"."""
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def apply_discount(price_cents: int, percent: int) -> int:
    """Aplica o desconto, arredondando meio centavo para cima."""
    return (price_cents * (100 - percent) + 50) // 100


def minimum_installment_cents(count: int) -> int:
    for max_count, minimum in INSTALLMENT_TIERS:
        if count <= max_count:
            return minimum
    raise ValueError(f"Máximo de {MAX_INSTALLMENTS} parcelas")


def installment_options(amount_cents: int) -> list[Installment]:
    """Parcelamentos sem juros permitidos. O pagamento em 1x não tem valor mínimo."""
    options = [Installment(1, amount_cents)]
    for count in range(2, MAX_INSTALLMENTS + 1):
        if amount_cents >= minimum_installment_cents(count) * count:
            options.append(Installment(count, (amount_cents + count // 2) // count))
    return options


def allows_combined_payment(amount_cents: int) -> bool:
    return amount_cents > COMBINED_PAYMENT_THRESHOLD_CENTS

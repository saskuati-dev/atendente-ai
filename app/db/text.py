"""Normalização de texto e contatos, usada na carga do banco e nas consultas."""

import re
import unicodedata


def normalize_text(text: str) -> str:
    """Remove acentos e caixa: "Violão Aço" -> "violao aco"."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_marks.casefold().split())


def search_tokens(text: str) -> list[str]:
    """Separa em palavras normalizadas: "Shelby SN-7C" -> ["shelby", "sn", "7c"]."""
    return re.findall(r"[^\W_]+", normalize_text(text))


def normalize_phone(phone: str) -> str:
    """Mantém só os dígitos, sem o código do país: "+55 (67) 99812-3456" -> "67998123456"."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits[2:]
    return digits

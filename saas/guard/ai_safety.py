"""Small fail-closed boundary for seller messages and narrated AI claims.

The assistant never needs provider credentials or marketplace secrets, and the
deterministic WB snapshot does not include financial proceeds or WB funding.
"""
import re

# Detect the high-risk common secret formats before persistence and model I/O.
# This is not a substitute for avoiding raw credentials in model context.
_CREDENTIAL = re.compile(
    r"(?i)(?:"
    r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"  # JWT / WB token
    r"|\bBearer\s+[A-Za-z0-9_.-]{12,}"                # OAuth-style bearer
    r"|\bsk-[A-Za-z0-9_-]{16,}"                       # Common provider key
    r"|(?:api[_ -]?key|token|токен|пароль|секрет)\s*[:=]\s*\S{12,}"
    r")"
)

# There is no sales volume, net proceeds, funding or margin in the input data.
# Even a number that coincides with a SKU discount is NOT evidence of revenue.
_UNSUPPORTED_FINANCE = re.compile(
    r"(?i)(?:\b(?:прибыл[ьи]|выручк\w*|марж\w*|"
    r"заработа\w*|экономи\w*|рентабел\w*|"
    r"окупаем\w*|доход\w*|финансиру\w*|"
    r"расход\w*|затрат\w*|комисс\w*)\b"
    r"|\b(?:rub|руб\w*)\b|₽"
    r"|за\s+сч[её]т\s+(?:wb|вб|wildberries))"
)


def contains_credential(text):
    return bool(isinstance(text, str) and _CREDENTIAL.search(text))


def has_unsupported_financial_claim(text):
    """Conservative: reject any model financial narration; local FAQ explains limits."""
    return not isinstance(text, str) or bool(_UNSUPPORTED_FINANCE.search(text))

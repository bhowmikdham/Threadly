"""Merchant query vocabulary: turn what a user *says* ("Guzman y Gomez",
"gyg", "Amazon") into terms that can hit what the extractor *stored*
(domain-derived merchant like GYG, plus the raw sender address kept in the
entity's value). Shared by the gateway and orchestrator query paths."""


def merchant_terms(phrase: str) -> list[str]:
    """Lowercased match terms for a merchant phrase: each meaningful word,
    the initialism ("Guzman y Gomez" -> gyg), and the collapsed phrase."""
    words = [w.strip(".,!?'\"").lower() for w in phrase.split()]
    words = [w for w in words if w]
    if not words:
        return []

    terms: list[str] = []

    def add(term: str):
        if len(term) >= 2 and term not in terms:
            terms.append(term)

    for word in words:
        add(word)
    if len(words) > 1:
        add("".join(w[0] for w in words))  # initialism
        add("".join(words))                # collapsed phrase
    return terms


def merchant_candidates(phrase: str) -> list[str]:
    """ILIKE patterns for the strict (substring) matching tier."""
    return [f"%{term}%" for term in merchant_terms(phrase)]


def fuzzy_terms(phrase: str) -> list[str]:
    """Terms eligible for the edit-distance fallback tier (typos like
    GIG -> GYG). Very short terms are excluded — one edit on a 2-char
    term matches half the alphabet."""
    return [term for term in merchant_terms(phrase) if len(term) >= 3]


# SQL fragment for the fallback tier: allow 1 edit on short names, 2 on long.
# Used with an unnested text[] of fuzzy_terms().
FUZZY_MERCHANT_SQL = (
    "EXISTS (SELECT 1 FROM unnest({param}::text[]) AS t(term) "
    "WHERE levenshtein(lower(merchant), term) <= CASE WHEN length(term) <= 5 THEN 1 ELSE 2 END)"
)

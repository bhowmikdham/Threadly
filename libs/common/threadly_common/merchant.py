"""Merchant query vocabulary: turn what a user *says* ("Guzman y Gomez",
"gyg", "Amazon") into ILIKE patterns that can hit what the extractor *stored*
(domain-derived merchant like GYG, plus the raw sender address kept in the
entity's value). Shared by the gateway and orchestrator query paths."""


def merchant_candidates(phrase: str) -> list[str]:
    """ILIKE patterns for a merchant phrase: each meaningful word, the
    initialism ("Guzman y Gomez" -> gyg), and the collapsed phrase."""
    words = [w.strip(".,!?'\"").lower() for w in phrase.split()]
    words = [w for w in words if w]
    if not words:
        return []

    patterns: list[str] = []

    def add(term: str):
        pat = f"%{term}%"
        if len(term) >= 2 and pat not in patterns:
            patterns.append(pat)

    for word in words:
        add(word)
    if len(words) > 1:
        add("".join(w[0] for w in words))  # initialism
        add("".join(words))                # collapsed phrase
    return patterns

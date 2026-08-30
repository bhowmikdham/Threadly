from threadly_common.merchant import fuzzy_terms, merchant_candidates, merchant_terms


def test_single_word():
    assert merchant_candidates("GYG") == ["%gyg%"]


def test_multiword_gets_initialism_and_collapsed():
    patterns = merchant_candidates("Guzman y Gomez")
    assert "%guzman%" in patterns and "%gomez%" in patterns
    assert "%gyg%" in patterns          # the initialism is what hits the stored merchant
    assert "%guzmanygomez%" in patterns
    assert "%y%" not in patterns        # single letters never become patterns


def test_empty_phrase():
    assert merchant_candidates("   ") == []


def test_terms_back_candidates():
    assert merchant_terms("Guzman y Gomez")[:2] == ["guzman", "gomez"]
    assert "gyg" in merchant_terms("Guzman y Gomez")


def test_fuzzy_terms_drop_too_short():
    assert fuzzy_terms("GIG") == ["gig"]
    assert fuzzy_terms("go") == []  # 2-char terms are too noisy for edit distance

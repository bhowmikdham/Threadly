from threadly_common.merchant import merchant_candidates


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

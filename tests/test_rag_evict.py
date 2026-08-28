from orch_rag_evict import select_evictions


def test_under_cap_keeps_everything():
    assert select_evictions([("a", 1.0), ("b", 2.0)], cap=5) == []


def test_evicts_oldest_beyond_cap():
    docs = [("old", 1.0), ("mid", 2.0), ("new", 3.0), ("newest", 4.0)]
    assert set(select_evictions(docs, cap=2)) == {"old", "mid"}


def test_zero_cap_is_a_noop_guard():
    assert select_evictions([("a", 1.0)], cap=0) == []

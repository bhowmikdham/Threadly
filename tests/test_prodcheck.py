import pytest
from threadly_common.prodcheck import assert_prod_config, find_config_problems


def test_dev_mode_is_a_noop():
    assert find_config_problems(True, {"JWT": "dev-secret"}) == []


def test_placeholders_and_missing_are_caught():
    problems = find_config_problems(False, {
        "THREADLY_JWT_SECRET": "change-me-32-chars-minimum-please",
        "THREADLY_INTERNAL_TOKEN": "",
        "THREADLY_DATABASE_URL": "postgresql://threadly:threadly-dev-password@postgres/threadly",
    })
    assert len(problems) == 3


def test_min_length_enforced():
    problems = find_config_problems(False, {"S": "short"}, min_lengths={"S": 32})
    assert problems == ["S must be at least 32 characters"]


def test_good_config_passes():
    assert find_config_problems(False, {"S": "a" * 64}, min_lengths={"S": 32}) == []


def test_assert_raises_with_service_name():
    with pytest.raises(RuntimeError, match=r"\[gateway\].*placeholder"):
        assert_prod_config("gateway", False, {"X": "dev-secret"})

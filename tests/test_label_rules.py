from inference_label_rules import rule_action, rule_category, rule_priority

BOSS = "Q3 report draft\nHey — can you send me the Q3 numbers by Thursday? Board pack is due Friday."
GYG = "Your GYG order confirmation #GYG-84721\nThanks for ordering! Total charged: $9.50"


def test_priority():
    assert rule_priority(BOSS)["label"] == "high"          # "by Thursday"
    assert rule_priority(GYG)["label"] == "normal"
    assert rule_priority("URGENT: server down")["label"] == "high"


def test_action():
    assert rule_action(BOSS)["label"] == "reply"           # "can you"
    assert rule_action(GYG)["label"] == "no_action"
    assert rule_action("Please review the attached proposal")["label"] == "review"
    assert rule_action("Meeting invite: standup Monday")["label"] == "attend"
    assert rule_action("Needs your sign-off before release")["label"] == "approve"


def test_category():
    assert rule_category(GYG)["label"] == "purchases"
    assert rule_category(BOSS)["label"] == "work"
    assert rule_category("Click unsubscribe to stop this newsletter")["label"] == "newsletters"
    assert rule_category("hey, long time no see!")["label"] == "other"


def test_shape_matches_classifier_contract():
    verdict = rule_priority("hello")
    assert set(verdict) == {"label", "confidence", "source"} and verdict["source"] == "rules"

from inference_pii import mask, unmask


def test_masks_emails_and_phones():
    text = "Contact priya@acme-corp.com or +61 412 345 678 about the invoice."
    masked, mapping = mask(text)
    assert "priya@acme-corp.com" not in masked
    assert "412 345 678" not in masked
    assert "<email_1>" in masked and "<phone_1>" in masked
    assert unmask(masked, mapping) == text


def test_repeated_pii_gets_same_placeholder():
    masked, mapping = mask("a@b.com wrote to a@b.com")
    assert masked.count("<email_1>") == 2
    assert len(mapping) == 1


def test_clean_text_untouched():
    masked, mapping = mask("no personal data here")
    assert masked == "no personal data here"
    assert mapping == {}

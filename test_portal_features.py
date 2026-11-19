"""Offline regressions for the additive staff, payment and policy features.

Run with APP_ENV=test using only disposable development databases:
    pytest -q test_portal_features.py
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import portal_features as features


def test_upi_payments_allow_positive_arbitrary_invoice_values():
    # There is no business-imposed 1,000 or 10,000 rupee cap.
    assert features.clean_amount("1") == Decimal("1")
    assert features.clean_amount("125000.75") == Decimal("125000.75")
    assert features.clean_amount("999999999.99") == Decimal("999999999.99")


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", "1.001", "zero"])
def test_non_payment_amounts_are_rejected(bad):
    with pytest.raises(HTTPException) as error:
        features.clean_amount(bad)
    assert error.value.status_code == 422


def test_published_policy_search_uses_only_matching_sources(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    entries = [
        {"id":"page:refund","title":"Refund Policy","body":"Approved requests are reviewed by the finance team."},
        {"id":"page:about","title":"Our Story","body":"We supply fresh milk and curd."},
    ]
    answer = features.assistant_answer("How does the refund policy work?",entries)
    assert answer["sources"]
    assert answer["sources"][0]["id"] == "page:refund"
    assert "refund" in answer["reply"].lower()
    assert not features.answer_from_content("xxxxxxxxxyyyyy",entries)


def test_unknown_questions_do_not_invent_company_rules():
    result = features.assistant_answer("Tell me about quantum mechanics",[])
    assert result["sources"] == []
    assert "can't confirm" in result["reply"].lower()


def test_public_website_omits_internal_media_ids():
    state = {
        "website_design": {
            "copy":{"hero_title":"Farm-fresh happiness"},
            "blocks":[{"id":"section-a","title":"Our promise","body":"Fresh milk.",
                       "page":"home","placement":"after-hero","variant":"feature","media_id":"MEDIA1"}],
            "media_id":"MEDIA1","version":3,
        },
        "campaign_media":{"MEDIA1":{"id":"MEDIA1","mime_type":"image/png"}},
    }
    public = features.site_view(state)
    assert public["blocks"][0]["media_url"] == "/media/campaign/MEDIA1"
    assert "media_id" not in public["blocks"][0]
    assert "media_id" not in public
    assert public["version"] == 3


def test_website_sections_limit_and_invalid_fields():
    data = {"version":1,"blocks":[{"id":"test","page":"home",
            "placement":"after-hero","title":"Hello"}]}
    assert features.SiteBody.model_validate(data).blocks[0].title == "Hello"
    with pytest.raises(ValidationError):
        features.SiteBody.model_validate({"version":1,"blocks":data["blocks"]*25})
    with pytest.raises(ValidationError):
        features.SiteBlock.model_validate({"id":"x","page":"admin",
          "placement":"after-hero","title":"Hello"})


def test_upi_receiving_id_validation_is_structural():
    assert features.UPI_PATTERN.fullmatch("company@bank")
    assert not features.UPI_PATTERN.fullmatch("https://example.com")
    assert not features.UPI_PATTERN.fullmatch("malicious@bank&pa=attacker@bank")


def test_admin_upi_permission_is_super_admin_only(monkeypatch):
    monkeypatch.setattr(features.c,"actor",lambda state,request,portal: ({"role":"finance","email":"staff@example.com"},None))
    with pytest.raises(HTTPException) as error:
        features.super_admin({},object())
    assert error.value.status_code == 403


def test_editor_role_can_publish_but_finance_cannot(monkeypatch):
    monkeypatch.setattr(features.c,"actor",lambda state,request,portal: ({"role":"finance","content_editor":True},None))
    assert features.editing_actor({},object())["content_editor"]
    monkeypatch.setattr(features.c,"actor",lambda state,request,portal: ({"role":"finance","content_editor":False},None))
    with pytest.raises(HTTPException) as error:
        features.editing_actor({},object())
    assert error.value.status_code == 403

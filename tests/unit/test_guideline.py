def test_topic_text_fills_template(repos):
    claim = repos.claims.get("CL-2048")
    txt = repos.guideline.topic_text("submission_method", claim)
    assert "CL-2048" in txt and "pathology report, office note" in txt and "member portal" in txt
    pt = repos.guideline.topic_text("processing_time_after_submission", claim)
    assert "usually less than a week" in pt and "restarts" in pt


def test_topics_requiring_documents_skip_claims_without_them(repos):
    auto = repos.claims.get("CL-2102")
    for topic in repos.guideline.topics():
        assert repos.guideline.topic_text(topic, auto) is None
    assert repos.guideline.case_type_guidance("auto").startswith("For auto claims")


def test_document_lookups_are_fuzzy(repos):
    key, text = repos.guideline.document_guidance("pathology report")
    assert key == "original pathology report" and "patient name" in text
    key2, alt = repos.guideline.document_alternative("office note")
    assert key2 == "treating provider office note" and "visit summary" in alt
    assert repos.guideline.document_guidance("dental x-ray") is None
    assert "human claims representative" in repos.guideline.human_review()
    assert "replacement copy" in repos.guideline.default_alternative()
    assert "separate claim-specific rule" in repos.guideline.fallback()

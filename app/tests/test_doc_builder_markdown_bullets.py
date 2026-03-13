from app.services.doc_builder import _build_word_style_para_elem_with_bold_lead


def test_build_word_style_para_elem_with_bold_lead_formats_service_label() -> None:
    elem = _build_word_style_para_elem_with_bold_lead(
        "**OCI File Storage:** Shared storage for workloads",
        "NormalBodyBullet1",
    )

    xml = elem.xml
    assert "w:pStyle" in xml
    assert 'w:val="NormalBodyBullet1"' in xml
    assert "<w:b/>" in xml
    assert "OCI File Storage:" in xml
    assert "Shared storage for workloads" in xml
    assert "**" not in xml


def test_build_word_style_para_elem_with_bold_lead_supports_colon_outside_bold() -> None:
    elem = _build_word_style_para_elem_with_bold_lead(
        "**OCI File Storage**: Shared storage for workloads",
        "NormalBodyBullet1",
    )

    xml = elem.xml
    assert "<w:b/>" in xml
    assert "OCI File Storage" in xml
    assert "Shared storage for workloads" in xml
    assert "**" not in xml

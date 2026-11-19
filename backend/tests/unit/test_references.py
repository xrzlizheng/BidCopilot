from app.modules.evaluation.references import (
    STATUS_DANGLING,
    STATUS_RESOLVED,
    STATUS_UNVERIFIABLE,
    check_references,
    extract_references,
    normalize_number,
    unresolved,
)


def test_extract_references_covers_three_kinds() -> None:
    references = extract_references("详见附件5。参见第2章。随附《CMMI5 证书》。")

    kinds = {reference["kind"] for reference in references}
    assert kinds == {"attachment", "section", "document"}
    assert all(reference["status"] == STATUS_UNVERIFIABLE for reference in references)


def test_check_references_resolves_existing_section() -> None:
    references = check_references("技术方案参见第2章。", {1, 2})
    statuses = {(r["kind"], r["target"]): r["status"] for r in references}

    assert statuses[("section", "2")] == STATUS_RESOLVED
    assert unresolved(references) == []


def test_check_references_flags_dangling_section() -> None:
    references = check_references("业绩详见第9章。", {1, 2})
    statuses = {(r["kind"], r["target"]): r["status"] for r in references}

    assert statuses[("section", "9")] == STATUS_DANGLING
    assert len(unresolved(references)) == 1


def test_check_references_ignores_tender_document_references() -> None:
    """引用招标文件的章节不是投标文件内部引用，不能误报为悬空。"""
    references = check_references("按招标文件第三章要求逐条响应。", {1, 2})

    assert references == []


def test_check_references_ignores_generic_document_names() -> None:
    references = check_references("详见《招标文件》与《投标文件》。", {1})

    assert references == []


def test_normalize_number_handles_chinese_numerals() -> None:
    assert normalize_number("三") == "3"
    assert normalize_number("十") == "10"
    assert normalize_number("十二") == "12"
    assert normalize_number("20") == "20"
    assert normalize_number("") == ""


def test_unresolved_keeps_only_actionable_items() -> None:
    references = check_references(
        "技术方案参见第1章。业绩详见第9章。资质详见附件3。", {1}
    )

    pending = unresolved(references)
    targets = {(reference["kind"], reference["target"]) for reference in pending}
    assert targets == {("section", "9"), ("attachment", "附件3")}

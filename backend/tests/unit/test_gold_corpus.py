from app.modules.tender.parser import build_constraint_set, parse_document
from tests.gold_corpus import build_gold_cases, evaluate_gold_corpus


def test_gold_corpus_schema() -> None:
    cases = build_gold_cases()
    assert [case.name for case in cases] == [
        "gov_it_table",
        "engineering_paragraph",
        "goods_table",
        "transport_merged_header",
        "serial_numbered_table",
        "qualification_table",
        "dark_bid_phrasing",
        "service_weight_percent",
        "invalid_bid_numbered_list",
        "two_table_appendix",
        "dark_bid_score_range",
        "txt_plain_text",
        "md_markdown_table",
        "pdf_text_layer",
        "pdf_table_scoring",
        "pdf_table_merged_header",
        "pdf_table_appendix",
        "multi_item_line",
        "factor_alias_table",
    ]
    suffix_magic = {".docx": b"PK", ".pdf": b"%PDF", ".txt": None, ".md": None}
    for case in cases:
        magic = suffix_magic[case.filename.rsplit(".", 1)[-1].join((".", ""))]
        assert magic is None or case.content.startswith(magic), case.filename
    assert {case.filename.rsplit(".", 1)[-1] for case in cases} == {"docx", "pdf", "txt", "md"}



def test_gold_corpus_metrics() -> None:
    results = {result.case: result for result in evaluate_gold_corpus()}

    for name, result in results.items():
        for dimension in ("scoring", "qualification", "reject"):
            prf = getattr(result, dimension)
            assert prf.f1 == 1.0, (name, dimension, prf)
            assert prf.recall == 1.0, (name, dimension, prf)
        assert result.budget_ok, name
        assert result.dark_bid_ok, name


def test_metric_distinguishes_precision_from_recall() -> None:
    from tests.gold_corpus import Prf, _match_clauses

    one_covers_two = _match_clauses(
        ("有效的营业执照", "物业服务一级资质"),
        [{"text": "投标人须具备有效的营业执照及物业服务一级资质。"}],
        "text",
    )
    assert one_covers_two.recall == 1.0
    assert one_covers_two.precision == 1.0
    assert one_covers_two.f1 == 1.0

    with_false_positive = _match_clauses(
        ("营业执照",),
        [{"text": "具备有效的营业执照"}, {"text": "无关条款"}],
        "text",
    )
    assert with_false_positive.recall == 1.0
    assert with_false_positive.precision == 0.5
    assert with_false_positive.f1 < 1.0

    assert Prf(expected=0, predicted=0, matched=0, covered=0).f1 == 1.0


def test_qualification_table_headings_not_extracted() -> None:
    case = next(case for case in build_gold_cases() if case.name == "qualification_table")
    constraints = build_constraint_set(parse_document(case.content, case.filename), {})
    texts = [item["text"] for item in constraints["qualification_requirements"]]
    assert not any("资格审查表" in text for text in texts)
    assert any("营业执照" in text for text in texts)


def test_merged_header_qualification_categories() -> None:
    case = next(case for case in build_gold_cases() if case.name == "transport_merged_header")
    constraints = build_constraint_set(parse_document(case.content, case.filename), {})
    categories = {item["category"] for item in constraints["qualification_requirements"]}
    assert categories == {"QUALIFICATION", "PERFORMANCE"}


def test_markdown_headings_are_not_extracted_as_requirements() -> None:
    case = next(case for case in build_gold_cases() if case.name == "md_markdown_table")
    constraints = build_constraint_set(parse_document(case.content, case.filename), {})
    texts = [item["text"] for item in constraints["qualification_requirements"]]
    assert not any(text.strip().startswith("#") for text in texts)
    assert not any(text.strip() == "资格要求" for text in texts)
    assert len(texts) == 2


def test_colon_lead_in_is_a_header_not_a_reject_clause() -> None:
    case = next(case for case in build_gold_cases() if case.name == "txt_plain_text")
    constraints = build_constraint_set(parse_document(case.content, case.filename), {})
    clauses = [item["text"] for item in constraints["reject_clauses"]]
    assert clauses, "list items under the lead-in must still be captured"
    assert not any(clause.rstrip().endswith(("：", ":")) for clause in clauses)
    assert len(clauses) == 2

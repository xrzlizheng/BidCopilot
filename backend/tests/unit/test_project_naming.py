"""项目命名口径回归测试：不能用公告标题当项目名。"""

from types import SimpleNamespace

from app.modules.projects.service import resolve_project_name


def _opp(title: str, project_name: str | None) -> SimpleNamespace:
    return SimpleNamespace(title=title, project_name=project_name)


def test_prefers_opportunity_project_name_over_announcement_title() -> None:
    """回归：从「更正公告」建项目时，名称应为标的名称，而不是公告标题。"""
    opp = _opp(
        "2026年度合肥高新区政务云服务项目更正公告",
        "2026年度合肥高新区政务云服务项目",
    )
    assert resolve_project_name(None, opp) == "2026年度合肥高新区政务云服务项目"


def test_falls_back_to_title_when_project_name_missing() -> None:
    """商机没有 project_name 时退回公告标题，不得得到空名。"""
    opp = _opp("某项目竞争性磋商公告", None)
    assert resolve_project_name(None, opp) == "某项目竞争性磋商公告"

    opp_blank = _opp("某项目竞争性磋商公告", "   ")
    assert resolve_project_name(None, opp_blank) == "某项目竞争性磋商公告"


def test_explicit_name_always_wins_and_is_trimmed() -> None:
    """用户显式填写的名称优先，且去首尾空白。"""
    opp = _opp("公告标题", "项目名称")
    assert resolve_project_name("  我自己起的名字  ", opp) == "我自己起的名字"
    # 显式传空串等同于未传，仍走商机回退
    assert resolve_project_name("   ", opp) == "项目名称"


def test_returns_empty_without_opportunity_and_without_explicit_name() -> None:
    assert resolve_project_name(None, None) == ""

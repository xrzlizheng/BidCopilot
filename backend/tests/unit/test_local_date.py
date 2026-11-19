import time
from datetime import UTC, datetime, timedelta

import pytest

from app.core.time import CST, business_today, local_date


def test_local_date_keeps_early_beijing_deadline_on_its_own_day() -> None:
    for hour, minute in ((9, 0), (8, 0), (7, 30), (2, 0), (0, 5)):
        stored = datetime(2027, 3, 5, hour, minute, tzinfo=CST).astimezone(UTC)
        assert local_date(stored) == datetime(2027, 3, 5).date(), (hour, minute)


def test_local_date_reproduces_the_utc_day_shift_it_fixes() -> None:
    early = datetime(2027, 3, 5, 7, 30, tzinfo=CST).astimezone(UTC)
    assert early.date() == datetime(2027, 3, 4).date()
    assert local_date(early) == datetime(2027, 3, 5).date()


def test_local_date_treats_naive_input_as_beijing_time() -> None:
    assert local_date(datetime(2027, 3, 5, 7, 30)) == datetime(2027, 3, 5).date()
    assert local_date(datetime(2027, 3, 5, 23, 0)) == datetime(2027, 3, 5).date()


def test_local_date_handles_non_cst_offsets() -> None:
    utc_late = datetime(2027, 3, 5, 23, 30, tzinfo=UTC)
    assert local_date(utc_late) == (utc_late + timedelta(hours=8)).date()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset is POSIX-only; cannot vary process TZ here")
def test_business_today_ignores_process_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = datetime.now(UTC).astimezone(CST).date()
    for name in ("Asia/Shanghai", "UTC", "US/Pacific"):
        monkeypatch.setenv("TZ", name)
        time.tzset()
        assert business_today() == expected, name


def test_business_today_is_not_taken_from_process_local_time() -> None:
    assert business_today() == datetime.now(UTC).astimezone(CST).date()
    assert business_today() is not None
    assert (datetime.now(UTC).astimezone(CST).date() - business_today()).days == 0

from datetime import UTC, date, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def local_date(value: datetime) -> date:
    """Calendar day in China Standard Time.

    Postgres normalises aware datetimes to UTC, so ``value.date()`` would report the
    previous day for any Beijing deadline earlier than 08:00.
    """
    aware = value if value.tzinfo is not None else value.replace(tzinfo=CST)
    return aware.astimezone(CST).date()


def business_today() -> date:
    """Today on the China business calendar, independent of the process timezone.

    ``date.today()`` follows the host TZ, so the same query returns a different
    expiry window on an Asia/Shanghai laptop and a UTC container.
    """
    return datetime.now(UTC).astimezone(CST).date()

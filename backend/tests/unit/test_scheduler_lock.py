"""调度器多实例互斥：执行锁未获锁时必须跳过本次执行。

多副本部署（K8s / SaaS）下每个实例都会构建调度器，互斥由
app.db.locks.advisory_lock 保证；这里用替身锁验证"未获锁即不执行"
的语义，不连接真实数据库。
"""

from contextlib import asynccontextmanager

from app.workers import scheduler


def _fake_lock(acquired: bool):
    @asynccontextmanager
    async def _lock(key: str):
        yield acquired

    return _lock


async def test_tick_skipped_when_lock_not_acquired(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run() -> int:
        calls.append("run")
        return 0

    monkeypatch.setattr(scheduler, "advisory_lock", _fake_lock(False))
    monkeypatch.setattr(scheduler, "run_due_subscriptions", fake_run)

    await scheduler._tick()

    assert calls == []


async def test_tick_executes_when_lock_acquired(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run() -> int:
        calls.append("run")
        return 1

    monkeypatch.setattr(scheduler, "advisory_lock", _fake_lock(True))
    monkeypatch.setattr(scheduler, "run_due_subscriptions", fake_run)

    await scheduler._tick()

    assert calls == ["run"]


async def test_milestone_skipped_when_lock_not_acquired(monkeypatch) -> None:
    used: list[str] = []

    class _FakeSession:
        async def __aenter__(self):
            used.append("enter")
            return self

        async def __aexit__(self, *args) -> bool:
            return False

    def fake_session_local():
        used.append("constructed")
        return _FakeSession()

    monkeypatch.setattr(scheduler, "advisory_lock", _fake_lock(False))
    monkeypatch.setattr("app.db.session.SessionLocal", fake_session_local)

    await scheduler._milestone_reminder()

    assert used == []


async def test_milestone_executes_when_lock_acquired(monkeypatch) -> None:
    scanned: list[object] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

    async def fake_scan(session, *, now):
        scanned.append(now)
        return 1

    monkeypatch.setattr(scheduler, "advisory_lock", _fake_lock(True))
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr("app.modules.contracts.service.scan_due_milestones", fake_scan)

    await scheduler._milestone_reminder()

    assert len(scanned) == 1


def test_build_scheduler_registers_both_jobs() -> None:
    built = scheduler.build_scheduler()

    job_ids = {job.id for job in built.get_jobs()}

    assert scheduler.JOB_ID in job_ids
    assert "contract-milestone-reminder" in job_ids

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.app.core.config import Settings
from backend.app.core.security import (
    ensure_identity,
    issue_session,
    resolve_client_address,
    token_hash,
)
from backend.app.db.models import AuthSession, Job, JobEvent
from backend.app.db.session import SessionLocal
from backend.app.providers.ai import AIProviderChain, AIRequest
from backend.app.repositories.jobs import JobRepository
from backend.app.services.structured_ai import generate_validated_report


def test_deployment_safety_rejects_unsafe_server_and_local_bindings():
    with pytest.raises(ValidationError, match="AUTH_MODE=admin"):
        Settings(environment="server", auth_mode="local")
    with pytest.raises(ValidationError, match="loopback"):
        Settings(environment="local", api_host="0.0.0.0")
    with pytest.raises(ValidationError, match="must not contain"):
        Settings(environment="test", allowed_origins=["*"])

    safe = Settings(
        environment="server",
        auth_mode="admin",
        admin_username="admin",
        admin_password="correct-horse-battery-staple",
        app_secret_key="x" * 48,
        cookie_secure=True,
        allowed_origins=["https://research.example.com"],
    )
    assert safe.environment == "server"


def test_trusted_proxy_header_is_only_used_for_a_trusted_direct_peer():
    assert resolve_client_address("10.0.0.8", "203.0.113.9", ["10.0.0.0/8"]) == "203.0.113.9"
    assert resolve_client_address("198.51.100.2", "203.0.113.9", ["10.0.0.0/8"]) == "198.51.100.2"
    assert resolve_client_address("10.0.0.8", "not-an-ip", ["10.0.0.0/8"]) == "10.0.0.8"


def test_admin_password_rotation_revokes_existing_sessions(session):
    first = Settings(
        environment="test",
        auth_mode="admin",
        admin_username="rotation-admin",
        admin_password="first-password-is-long-enough",
    )
    user = ensure_identity(session, first)
    raw = issue_session(session, user, 24)
    assert session.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(raw))) is not None

    rotated = Settings(
        environment="test",
        auth_mode="admin",
        admin_username="rotation-admin",
        admin_password="second-password-is-also-long",
    )
    ensure_identity(session, rotated)
    assert (
        session.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user.id))
        == 0
    )


def test_job_event_sequence_uses_job_cursor(session):
    job = Job(kind="market_radar.refresh", payload={})
    session.add(job)
    session.flush()
    repository = JobRepository(session)
    repository.append_event(job, "queued", None)
    repository.append_event(job, "running", None)
    session.commit()
    events = session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.sequence)
    ).all()
    assert [event.sequence for event in events] == [1, 2]
    assert session.get(Job, job.id).event_sequence == 2


def test_two_sqlite_workers_cannot_claim_the_same_job(session):
    job = Job(kind="market_radar.refresh", payload={})
    session.add(job)
    session.commit()
    barrier = Barrier(2)

    def claim() -> str | None:
        with SessionLocal() as worker_session:
            barrier.wait()
            claimed = JobRepository(worker_session).claim_next()
            return claimed.id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_ids = list(pool.map(lambda _index: claim(), range(2)))

    assert claimed_ids.count(job.id) == 1
    assert claimed_ids.count(None) == 1


def test_ten_concurrent_sse_clients_receive_the_same_terminal_event(client, session):
    job = Job(
        kind="market_radar.refresh",
        status="completed",
        payload={},
        result={"ok": True},
        event_sequence=1,
    )
    session.add(job)
    session.flush()
    session.add(
        JobEvent(
            job_id=job.id,
            sequence=1,
            event_type="completed",
            payload={"ok": True},
        )
    )
    session.commit()

    def receive() -> tuple[int, str]:
        response = client.get(
            f"/api/v1/jobs/{job.id}/events",
            headers={"Last-Event-ID": "0"},
        )
        return response.status_code, response.text

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _index: receive(), range(10)))

    assert all(status == 200 for status, _body in responses)
    assert all("id: 1" in body and "event: completed" in body for _status, body in responses)
    resumed = client.get(
        f"/api/v1/jobs/{job.id}/events",
        headers={"Last-Event-ID": "1"},
    )
    assert resumed.status_code == 200
    assert resumed.text == ""


@dataclass
class FakeAIProvider:
    key: str
    model: str
    outputs: list[str | Exception]
    last_provider: str | None = None
    last_model: str | None = None
    last_attempts: list[dict] = field(default_factory=list)
    fallback_reason: str | None = None
    calls: int = 0

    async def generate(self, _request: AIRequest) -> str:
        self.calls += 1
        self.last_provider = self.key
        self.last_model = self.model
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            self.last_attempts = [{"provider": self.key, "outcome": "error"}]
            raise output
        self.last_attempts = [{"provider": self.key, "outcome": "success"}]
        return output


class AuthenticationFailure(Exception):
    status_code = 401


@pytest.mark.asyncio
async def test_ai_chain_falls_back_only_for_recoverable_failures():
    primary = FakeAIProvider("deepseek", "deepseek-chat", [TimeoutError("timeout")])
    backup = FakeAIProvider("siliconflow", "backup-model", ["ok"])
    chain = AIProviderChain([primary, backup])  # type: ignore[list-item]
    assert await chain.generate(AIRequest(system_prompt="s", user_prompt="u")) == "ok"
    assert chain.last_provider == "siliconflow"
    assert chain.fallback_reason == "deepseek:TimeoutError"

    primary_auth = FakeAIProvider("deepseek", "deepseek-chat", [AuthenticationFailure("bad key")])
    unused = FakeAIProvider("siliconflow", "backup-model", ["must not run"])
    chain = AIProviderChain([primary_auth, unused])  # type: ignore[list-item]
    with pytest.raises(AuthenticationFailure):
        await chain.generate(AIRequest(system_prompt="s", user_prompt="u"))
    assert unused.calls == 0


@pytest.mark.asyncio
async def test_structural_validation_failure_does_not_switch_provider():
    authoritative = {"input_freshness": "fresh", "score": 1}
    primary = FakeAIProvider("deepseek", "deepseek-chat", ["not-json", "still-not-json"])
    backup_payload = json.dumps({"unexpected": True})
    backup = FakeAIProvider("siliconflow", "backup-model", [backup_payload])
    chain = AIProviderChain([primary, backup])  # type: ignore[list-item]
    report = await generate_validated_report(
        chain,
        system_prompt="system",
        user_prompt="user",
        authoritative_rule_fields=authoritative,
        snapshot={"subject": "test"},
    )
    assert report.successful is False
    assert primary.calls == 2
    assert backup.calls == 0


def test_invalid_job_payload_is_rejected_before_enqueue(client, session):
    invalid_code = client.post(
        "/api/v1/jobs",
        json={"kind": "stock.analyze", "payload": {"code": "ABC"}},
    )
    invalid_range = client.post(
        "/api/v1/jobs",
        json={
            "kind": "data.refresh",
            "payload": {"start_date": "2026-07-30", "end_date": "2026-07-01"},
        },
    )
    assert invalid_code.status_code == 422
    assert invalid_range.status_code == 422
    assert session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize(
    "rank_type",
    ["top_gainers", "top_losers", "top_volume", "top_amount", "top_turnover", "dragon_tiger"],
)
def test_generic_job_contract_accepts_all_hot_rankings(client, rank_type):
    response = client.post(
        "/api/v1/jobs",
        json={"kind": "screen.hot", "payload": {"rank_type": rank_type}},
    )

    assert response.status_code == 202


def test_login_rate_limit_returns_429_and_retry_after(client, session):
    from backend.app.api.deps import settings_dep
    from backend.app.main import app

    settings = Settings(
        environment="test",
        auth_mode="admin",
        admin_username="rate-limit-admin",
        admin_password="correct-password-for-rate-limit",
        login_max_attempts=2,
        login_window_seconds=60,
    )
    ensure_identity(session, settings)
    app.dependency_overrides[settings_dep] = lambda: settings
    try:
        for _ in range(2):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "rate-limit-admin", "password": "wrong-password"},
            )
            assert response.status_code == 401
        limited = client.post(
            "/api/v1/auth/login",
            json={"username": "rate-limit-admin", "password": "wrong-password"},
        )
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
    finally:
        app.dependency_overrides.pop(settings_dep, None)


def test_non_retryable_job_error_stops_after_first_attempt(session):
    job = Job(kind="market_radar.refresh", status="running", payload={}, attempts=1, max_attempts=3)
    session.add(job)
    session.commit()

    JobRepository(session).fail(job, "前置数据落后", retryable=False)

    assert job.status == "failed"
    assert job.finished_at is not None
    assert job.message == "任务失败"

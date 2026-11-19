import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_runtime_decision_settings_are_validated_and_persisted() -> None:
    tenant_id, _, email = await seed_tenant_user("runtime-settings")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            initial = await client.get("/api/v1/system/decision-settings", headers=headers)
            assert initial.status_code == 200, initial.text
            assert initial.json()["source"]["cost_model"] == "missing"

            saved = await client.put(
                "/api/v1/system/decision-settings",
                json={
                    "weights": {
                        "qualification": 40,
                        "economics": 20,
                        "cost": 20,
                        "risk": 10,
                        "resource": 10,
                    },
                    "thresholds": {"invest_min": 75, "pending_min": 55},
                    "cost_model": {
                        "direct_rate": 0.62,
                        "indirect_rate": 0.19,
                        "tax_rate": 0.09,
                    },
                    "reminders": {"enabled": True, "offsets": [10, 5, 2], "buffer_days": 3},
                },
                headers=headers,
            )
            assert saved.status_code == 200, saved.text
            body = saved.json()
            assert body["weights"]["qualification"] == 40
            assert body["thresholds"]["invest_min"] == 75
            assert body["cost_model"]["tax_rate"] == 0.09
            assert body["reminders"]["offsets"] == [10, 5, 2]
            assert body["source"]["cost_model"] == "tenant-setting"

            persisted = await client.get("/api/v1/system/decision-settings", headers=headers)
            assert persisted.json()["thresholds"]["pending_min"] == 55

            invalid = await client.put(
                "/api/v1/system/decision-settings",
                json={"thresholds": {"invest_min": 50, "pending_min": 80}},
                headers=headers,
            )
            assert invalid.status_code == 422, invalid.text
    finally:
        await cleanup_tenants([tenant_id])

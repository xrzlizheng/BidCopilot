import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_policy_sources_verify_never_fakes_verified() -> None:
    tenant_id, _, email = await seed_tenant_user("policy-sources")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            sources = await client.get("/api/v1/policy-sources", headers=headers)
            assert sources.status_code == 200, sources.text
            titles = {row["title"] for row in sources.json()["sources"]}
            assert "中华人民共和国招标投标法" in titles

            verified = await client.post(
                "/api/v1/policy-sources/verify",
                json={
                    "texts": [
                        "依据《中华人民共和国招标投标法》第三十三条，不得以低于成本的报价竞标。",
                        "依据《某虚构管理办法》第十条。",
                        "普通描述，没有政策引用。",
                    ]
                },
                headers=headers,
            )
            assert verified.status_code == 200, verified.text
            rows = verified.json()["results"]
            assert rows[0]["status"] == "verified"
            assert rows[0]["source"]["doc_no"] == "主席令第86号"
            assert rows[1]["status"] == "out_of_corpus"
            assert rows[1]["source"] is None
            assert rows[2]["status"] == "not_found"
    finally:
        await cleanup_tenants([tenant_id])

def test_capability_map_requires_account_and_does_not_claim_live_access(client):
    assert client.get("/market-data/capabilities").status_code == 401
    registered = client.post("/auth/register", json={
        "email": "capability-reader@example.com", "password": "a-strong-test-password",
    })
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    response = client.get("/market-data/capabilities", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    matrix = response.json()
    assert matrix["provider_configured"] is False
    assert len(matrix["assets"]) == 5
    assert {asset["snapshot_quote"] for asset in matrix["assets"]} == {"unconfigured"}
    assert {asset["live_order"] for asset in matrix["assets"]} == {"disabled"}
    assert "access_token" not in response.text

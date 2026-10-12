def _register(client, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/register", json={"email": email, "password": "a-long-test-password"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_strategy_presets_are_validated_persisted_and_user_scoped(client):
    assert client.get("/paper/strategy-presets").status_code == 401
    headers = _register(client, "presets@example.com")
    payload = {
        "name": "  PCR spread setup  ",
        "strategy_key": "bull_call_spread",
        "entry_conditions": [
            {"metric": "put_call_ratio_oi", "operator": "GTE", "threshold": "1.2"}
        ],
    }

    created = client.post("/paper/strategy-presets", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "PCR spread setup"
    assert created.json()["entry_conditions"][0]["threshold"] == "1.2"
    preset_id = created.json()["id"]

    assert client.post("/paper/strategy-presets", headers=headers, json=payload).status_code == 409
    assert (
        client.post(
            "/paper/strategy-presets",
            headers=headers,
            json={**payload, "name": "Custom", "strategy_key": "custom"},
        ).status_code
        == 422
    )

    other_user = _register(client, "other-presets@example.com")
    assert client.get("/paper/strategy-presets", headers=other_user).json() == []
    assert (
        client.delete(f"/paper/strategy-presets/{preset_id}", headers=other_user).status_code == 404
    )

    stored = client.get("/paper/strategy-presets", headers=headers)
    assert stored.status_code == 200
    assert len(stored.json()) == 1
    assert stored.json()[0]["id"] == preset_id
    assert client.delete(f"/paper/strategy-presets/{preset_id}", headers=headers).status_code == 204
    assert client.get("/paper/strategy-presets", headers=headers).json() == []

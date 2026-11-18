import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "online"

def test_public_metadata_keeps_limits_without_reading_private_state(monkeypatch):
    import secure_backend as secure
    hits = []
    monkeypatch.setattr(secure, 'rate_limit', lambda bucket, limit, seconds: hits.append((bucket, limit, seconds)))
    monkeypatch.setattr(secure, 'read_transaction', lambda: pytest.fail('public metadata read the state'))
    status = client.get('/')
    config = client.get('/config')
    assert status.status_code == config.status_code == 200
    assert status.json()['version'] == app.version
    assert config.json()['currency'] == 'INR'
    assert len(hits) == 2 and all(bucket.startswith('ip:') for bucket, _, _ in hits)
    assert config.headers['Cache-Control'] == 'no-store'
    assert config.headers['X-Content-Type-Options'] == 'nosniff'

def test_admin_unauthorized_access():
    response = client.post("/admin/generate-deliveries", headers={"x-admin-key": "invalid_key"})
    assert response.status_code == 403

def test_driver_unauthorized_access():
    response = client.get("/driver/deliveries", headers={"x-driver-key": "invalid_key"})
    assert response.status_code == 403

def test_payment_webhook():
    payload = {
        "event": "payment.succeeded",
        "order_id": "test-order-123",
        "transaction_id": "tx-9999",
        "amount": 150.0,
        "status": "paid"
    }
    response = client.post("/webhooks/payment", json=payload)
    # Original unauthenticated-success assertions are retained for traceability:
    # assert response.status_code == 200
    # assert response.json()["status"] == "success"
    # An unsigned webhook must never be accepted as a real payment.
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]

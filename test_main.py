import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "online"

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
    assert response.status_code == 200
    assert response.json()["status"] == "success"

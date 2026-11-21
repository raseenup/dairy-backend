"""Offline product information regressions: dates, auth and versioned updates."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import app
from product_information import ManufacturerInformation, ProductInformationUpdate
from test_secure_flows import account

client=TestClient(app)

def sample(version=1):
    return {
        "version":version,
        "highlights":["Chilled dairy product","Store as directed"],
        "specifications":[{"label":"Pack size","value":"500 mL"}],
        "description":"Product details supplied by First Milk Venture.",
        "warranty":"",
        "manufacturer":{
            "manufacturer_name":"Example dairy","country_of_origin":"India",
            "batch_number":"BATCH-X","manufactured_on":"2026-11-21",
            "expiry_date":"2026-11-27",
            "storage_instructions":"Refrigerate according to package guidance."
        },
        "faqs":[{"question":"How should it be stored?","answer":"Refer to the pack label."}]
    }

def test_manufacture_and_expiry_validation():
    assert ManufacturerInformation(manufactured_on="2026-11-21",
                                   expiry_date="2026-11-27").expiry_date=="2026-11-27"
    for fields in [
        {"manufactured_on":"2026-11-30","expiry_date":"2026-11-21"},
        {"expiry_date":"2026-02-30"},
        {"manufactured_on":"not-a-date"},
        {"best_before_date":"2026-11-20","manufactured_on":"2026-11-21"},
    ]:
        with pytest.raises(ValidationError):
            ManufacturerInformation.model_validate(fields)
    with pytest.raises(ValidationError):
        ProductInformationUpdate.model_validate({**sample(),"highlights":["x"]*13})

def test_admin_can_publish_and_customer_can_read():
    admin,_=account("catalogadmin@example.com",portal="admin")
    customer,_=account("catalogcustomer@example.com",portal="customer",phone="9876543211")
    path="/products/prod-4/information"
    assert client.get(path).status_code==401
    initial=client.get(path,headers=customer)
    assert initial.status_code==200
    assert initial.json()["information"]=={}
    written=client.patch("/admin"+path,headers=admin,json=sample(initial.json()["version"]))
    assert written.status_code==200,written.text
    loaded=client.get(path,headers=customer)
    assert loaded.status_code==200
    data=loaded.json()["information"]
    assert data["manufacturer"]["expiry_date"]=="2026-11-27"
    assert data["manufacturer"]["batch_number"]=="BATCH-X"
    assert data["specifications"][0]["label"]=="Pack size"
    assert "updated_by" not in written.json().get("product",{})
    stale=client.patch("/admin"+path,headers=admin,json=sample(initial.json()["version"]))
    assert stale.status_code==409
    assert client.patch("/admin"+path,headers=customer,json=sample(loaded.json()["version"])).status_code in {401,403}

def test_designated_content_manager_and_restricted_finance():
    content,_=account("catalogcontent@example.com",portal="admin",role="content_manager")
    finance,_=account("catalogfinance@example.com",portal="admin",phone="9876543211",role="finance")
    url="/admin/products/prod-4/information"
    assert client.patch(url,headers=finance,json=sample()).status_code==403
    assert client.patch(url,headers=content,json=sample()).status_code==200

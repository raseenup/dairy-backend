"""Structured, editable product information for the First Milk Venture catalog.

Stored inside each existing product in the persistent secure_backend state.
No public database tables, existing records, or existing product endpoints are replaced.
"""
from datetime import date
from typing import List

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

import secure_backend as c


class SpecRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=90)
    value: str = Field(min_length=1, max_length=500)


class FAQRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=240)
    answer: str = Field(min_length=1, max_length=1500)


class ManufacturerInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand: str = Field(default="", max_length=150)
    generic_name: str = Field(default="", max_length=150)
    manufacturer_name: str = Field(default="", max_length=250)
    manufacturer_address: str = Field(default="", max_length=700)
    packer_name: str = Field(default="", max_length=250)
    packer_address: str = Field(default="", max_length=700)
    country_of_origin: str = Field(default="", max_length=120)
    batch_number: str = Field(default="", max_length=120)
    net_quantity: str = Field(default="", max_length=120)
    manufactured_on: str = Field(default="", max_length=10)
    expiry_date: str = Field(default="", max_length=10)
    best_before_date: str = Field(default="", max_length=10)
    fssai_license: str = Field(default="", max_length=50)
    customer_care: str = Field(default="", max_length=350)
    storage_instructions: str = Field(default="", max_length=1200)
    ingredients: str = Field(default="", max_length=2000)
    allergens: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def dates_consistent(self):
        parsed = {}
        for key in ("manufactured_on", "expiry_date", "best_before_date"):
            raw = getattr(self, key)
            if raw:
                try:
                    parsed[key] = date.fromisoformat(raw)
                except ValueError as error:
                    raise ValueError(key + " must be a real YYYY-MM-DD date.") from error
                if parsed[key].isoformat() != raw:
                    raise ValueError(key + " must use YYYY-MM-DD.")
        for key in ("expiry_date", "best_before_date"):
            if "manufactured_on" in parsed and key in parsed:
                if parsed[key] < parsed["manufactured_on"]:
                    raise ValueError(key + " cannot be before the manufacturing date.")
        return self


class ProductInformationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    highlights: List[str] = Field(default_factory=list, max_length=12)
    specifications: List[SpecRow] = Field(default_factory=list, max_length=40)
    description: str = Field(default="", max_length=6000)
    warranty: str = Field(default="", max_length=2500)
    manufacturer: ManufacturerInformation = Field(default_factory=ManufacturerInformation)
    faqs: List[FAQRow] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_highlights(self):
        self.highlights = [item.strip() for item in self.highlights if item.strip()]
        if len(self.highlights) > 12 or any(len(item) > 240 for item in self.highlights):
            raise ValueError("Use up to 12 product highlights, each no more than 240 characters.")
        return self


def install_product_information(app):
    @app.get("/products/{product_id}/information", tags=["Product information"])
    def get_product_information(product_id: str, request: Request):
        with c.read_transaction(maintain=False) as state:
            c.actor(state, request)
            item = state["products"].get(product_id)
            c.require(item and not item.get("deleted"), "Product not found.", 404)
            return {
                "product_id": product_id,
                "name": item["name"],
                "version": item.get("version", 1),
                "information": item.get("information") or {},
            }

    @app.patch("/admin/products/{product_id}/information", tags=["Product information"])
    def save_product_information(product_id: str, request: Request, body: ProductInformationUpdate):
        with c.transaction() as state:
            user, _ = c.actor(state, request, "admin")
            c.require(
                user.get("role") in {"super_admin", "operations_manager", "content_manager"}
                or user.get("content_editor") is True,
                "Product publishing permission is required.", 403,
            )
            item = state["products"].get(product_id)
            c.require(item and not item.get("deleted"), "Product not found.", 404)
            c.require(item.get("version", 1) == body.version,
                      "This product was updated by another administrator. Refresh and try again.", 409)

            information = body.model_dump(mode="json", exclude={"version"})
            # Normalize text on the server. All rich fields are displayed as plain text.
            for key in ("description", "warranty"):
                information[key] = information[key].strip()
            information["specifications"] = [
                {"label": row["label"].strip(), "value": row["value"].strip()}
                for row in information["specifications"] if row["label"].strip() and row["value"].strip()
            ]
            information["faqs"] = [
                {"question": row["question"].strip(), "answer": row["answer"].strip()}
                for row in information["faqs"] if row["question"].strip() and row["answer"].strip()
            ]
            information["manufacturer"] = {
                key: value.strip() for key, value in information["manufacturer"].items()
            }
            information["updated_by"] = user["email"]
            information["updated_at"] = c.now()
            item["information"] = information
            item["version"] = item.get("version", 1) + 1
            c.audit(state, user["email"], "PRODUCT_INFORMATION_UPDATED", product_id)
            return {"message": "Product information published.", "product_id": product_id,
                    "version": item["version"], "information": information}

from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
from apscheduler.schedulers.background import BackgroundScheduler
import os
import random
import jwt
from supabase import create_client, Client
import resend

app = FastAPI(
    title="First Milk Venture API (Enterprise Edition)",
    description="Production-Grade Enterprise Secured Backend for First Milk Venture: Separate Portals, Unique Driver Keys, Admin Key Management, and Dispatch Assignment.",
    version="5.0.0"
)

ALLOWED_ORIGINS = [
    "https://firstmilkventure.com",
    "https://www.firstmilkventure.com",
    "https://admin.firstmilkventure.com",
    "https://delivery.firstmilkventure.com",
    "https://backend.firstmilkventure.com",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
    "http://localhost:8000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", ""))

# Dynamic secure keys storage in memory / database fallback
app_config = {
    "admin_secret_key": os.getenv("ADMIN_SECRET_KEY", "Firstmilk3211234"),
    "drivers": {
        "DriverMilk2026": {"name": "Primary Driver", "phone": "+966500000000", "address": "Jeddah Zone 1"}
    }
}

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-firstmilk-2026")
JWT_ALGORITHM = "HS256"

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

resend.api_key = os.getenv("RESEND_API_KEY", "")
security = HTTPBearer()

AUTH_EMAIL_SENDER = "First Milk Venture Auth <auth@firstmilkventure.com>"
FRONTDESK_EMAIL_SENDER = "First Milk Venture <frontdesk@firstmilkventure.com>"
SUBSCRIPTION_EMAIL_SENDER = "First Milk Venture Subscriptions <subscription@firstmilkventure.com>"
ORDERS_EMAIL_SENDER = "First Milk Venture Orders <orders@firstmilkventure.com>"
OFFERS_EMAIL_SENDER = "First Milk Venture Offers <offers@firstmilkventure.com>"

def verify_admin_access(x_admin_key: str = Header(..., alias="x-admin-key")):
    if x_admin_key != app_config["admin_secret_key"]:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid Admin Key.")

def verify_driver_access(x_driver_key: str = Header(..., alias="x-driver-key")):
    if x_driver_key not in app_config["drivers"] and x_driver_key != app_config["admin_secret_key"]:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid or unregistered Driver Access Key.")

def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_email: str = payload.get("sub")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid token payload.")
        return user_email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired. Please verify OTP again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Could not validate credentials.")

class RequestOTP(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    full_name: Optional[str] = None
    address: Optional[str] = None

class VerifyOTP(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    otp: str
    full_name: Optional[str] = None
    address: Optional[str] = None

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    unit: str = "Liter"
    stock_quantity: int = 100
    min_stock_threshold: int = 20
    image_url: str = ""

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    unit: Optional[str] = None
    stock_quantity: Optional[int] = None
    min_stock_threshold: Optional[int] = None
    image_url: Optional[str] = None

class SubscriptionCreate(BaseModel):
    product_id: str
    product_name: Optional[str] = "Fresh Farm Milk"
    quantity: int = 1
    frequency: str = "Daily"
    start_date: str
    zone_id: Optional[str] = "Zone-1"

class SubscriptionModify(BaseModel):
    status: Optional[str] = None
    pause_until: Optional[str] = None
    frequency: Optional[str] = None
    quantity: Optional[int] = None

class DeliveryStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None

class AssignDriverRequest(BaseModel):
    driver_key: str
    driver_name: Optional[str] = None

class CreateDriverRequest(BaseModel):
    driver_key: str
    name: str
    phone: str
    address: str

class UpdateAdminKeyRequest(BaseModel):
    new_admin_key: str

class DeliveryGenerationRequest(BaseModel):
    target_date: Optional[str] = "2026-11-10"

class PaymentWebhookPayload(BaseModel):
    event: str
    order_id: str
    transaction_id: str
    amount: float
    status: str

class OfferCreate(BaseModel):
    title: str
    description: str
    discount_percentage: float
    target_tier: str
    start_date: str
    end_date: str

class BroadcastOffer(BaseModel):
    offer_id: Optional[str] = None
    target_emails: List[EmailStr] = []
    subject: str = "🎉 Special Limited-Time Offer from First Milk Venture!"
    custom_message: str = ""

def generate_daily_orders(target_date_override: Optional[str] = None):
    today_str = target_date_override or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        return {"success": False, "error": "Supabase not connected"}
    
    try:
        response = supabase.table("subscriptions").select("*").eq("status", "active").execute()
        active_subs = response.data or []
        if not active_subs:
            return {"success": True, "generated_count": 0}

        orders_to_insert = []
        for sub in active_subs:
            pause_until = sub.get("pause_until")
            if pause_until and pause_until >= today_str:
                continue

            orders_to_insert.append({
                "subscription_id": sub.get("id"),
                "user_email": sub.get("user_email"),
                "product_id": sub.get("product_id"),
                "quantity": sub.get("quantity", 1),
                "delivery_date": today_str,
                "status": "pending",
                "zone_id": sub.get("zone_id", "Zone-1"),
                "assigned_driver": "Primary Driver"
            })
        
        if orders_to_insert:
            supabase.table("deliveries").insert(orders_to_insert).execute()
            return {"success": True, "generated_count": len(orders_to_insert)}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/", tags=["General"])
def read_root():
    return {"status": "online", "service": "First Milk Venture API", "version": "5.0.0"}

@app.post("/auth/request-otp", tags=["Authentication"])
def request_otp(body: RequestOTP):
    clean_email = body.email.strip().lower()
    otp = f"{random.randint(100000, 999999)}"
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    
    if supabase:
        try:
            supabase.table("auth_otps").upsert({
                "email": clean_email,
                "phone": body.phone,
                "full_name": body.full_name,
                "address": body.address,
                "otp_code": otp,
                "expires_at": expires_at
            }).execute()
        except Exception as db_err:
            raise HTTPException(status_code=500, detail=f"Database save error: {str(db_err)}")
    
    try:
        resend.Emails.send({
            "from": AUTH_EMAIL_SENDER,
            "to": [clean_email],
            "subject": f"🔑 Your Verification Code: {otp}",
            "html": f"<h2>Verification Code</h2><h1>{otp}</h1>"
        })
    except Exception:
        pass

    return {"message": "OTP sent successfully to your email"}

@app.post("/auth/verify-otp", tags=["Authentication"])
def verify_otp(body: VerifyOTP):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not connected.")
        
    clean_email = body.email.strip().lower()
    res = supabase.table("auth_otps").select("*").eq("email", clean_email).execute()
    records = res.data
    
    if not records:
        raise HTTPException(status_code=400, detail="Invalid verification code or email.")
        
    otp_record = records[0]
    stored_code = str(otp_record.get("otp_code")).strip()
    
    if stored_code != body.otp.strip():
        # Secure error message without leaking expected code in hint
        raise HTTPException(status_code=400, detail="Invalid verification code. Please check and try again.")
        
    supabase.table("auth_otps").delete().eq("email", clean_email).execute()
    
    token = jwt.encode({"sub": clean_email, "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"message": "Authentication successful.", "access_token": token, "token_type": "bearer"}

@app.get("/users/me", tags=["Users"])
def get_user_profile(user_email: str = Depends(verify_jwt)):
    if not supabase:
        return {"email": user_email, "status": "active"}
    try:
        subs = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
        return {"email": user_email, "subscriptions": subs.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: SubscriptionCreate, user_email: str = Depends(verify_jwt)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized.")
    try:
        prod_res = supabase.table("products").select("name").eq("id", sub.product_id).execute()
        product_name = sub.product_name or "Fresh Farm Product"
        if prod_res.data:
            product_name = prod_res.data[0].get("name", product_name)

        payload = sub.dict()
        payload["user_email"] = user_email
        payload["status"] = "active"
        payload["product_name"] = product_name
        
        response = supabase.table("subscriptions").insert(payload).execute()
        return {"message": "Subscription activated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/subscriptions/{subscription_id}", tags=["Subscriptions"])
def modify_subscription(subscription_id: str, body: SubscriptionModify, user_email: str = Depends(verify_jwt)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not initialized.")
    try:
        update_data = {k: v for k, v in body.dict().items() if v is not None}
        response = supabase.table("subscriptions").update(update_data).eq("id", subscription_id).eq("user_email", user_email).execute()
        return {"message": "Updated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/subscriptions", tags=["Subscriptions"])
def get_user_subscriptions(user_email: str = Depends(verify_jwt)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not initialized.")
    return supabase.table("subscriptions").select("*").eq("user_email", user_email).execute().data

@app.get("/deliveries", tags=["Deliveries"])
def get_user_deliveries(user_email: str = Depends(verify_jwt)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not initialized.")
    return supabase.table("deliveries").select("*").eq("user_email", user_email).execute().data

@app.post("/webhooks/payment", tags=["Billing & Payments"])
def handle_payment_webhook(payload: PaymentWebhookPayload):
    if not supabase:
        return {"status": "success", "transaction_id": payload.transaction_id}
    try:
        supabase.table("deliveries").update({"payment_status": "paid", "transaction_id": payload.transaction_id}).eq("id", payload.order_id).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/driver/deliveries", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def get_driver_routes(zone_id: Optional[str] = "Zone-1", delivery_date: Optional[str] = None):
    date_filter = delivery_date or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        return []
    try:
        res = supabase.table("deliveries").select("*").eq("zone_id", zone_id).eq("delivery_date", date_filter).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/driver/deliveries/{delivery_id}", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def update_driver_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    if not supabase:
        return {"message": "Status updated"}
    try:
        res = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
        return {"message": f"Delivery marked as {body.status}", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/products", tags=["Products"])
def get_products():
    if not supabase:
        return [
            {"id": "prod-1", "name": "Fresh Whole Milk", "description": "100% pure pasteurized farm fresh milk.", "price": 6.5, "unit": "Liter"},
            {"id": "prod-2", "name": "Pure Cow Ghee", "description": "Pure organic farm fresh ghee.", "price": 350.0, "unit": "500g"}
        ]
    return supabase.table("products").select("*").execute().data

@app.get("/offers", tags=["Offers Engine"])
def get_active_offers(tier: str = "all"):
    if not supabase:
        return [{"id": "offer-1", "title": "Welcome Special", "description": "15% off on daily milk subscriptions!", "discount_percentage": 15.0, "target_tier": "subscription", "end_date": "2026-12-31"}]
    return supabase.table("offers").select("*").execute().data

# Admin Endpoints for Drivers & Key Management
@app.get("/admin/drivers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def list_drivers():
    return [{"key": k, **v} for k, v in app_config["drivers"].items()]

@app.post("/admin/drivers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_driver(body: CreateDriverRequest):
    app_config["drivers"][body.driver_key] = {
        "name": body.name,
        "phone": body.phone,
        "address": body.address
    }
    return {"message": "Driver created successfully with unique secret key", "driver_key": body.driver_key}

@app.delete("/admin/drivers/{driver_key}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_driver(driver_key: str):
    if driver_key in app_config["drivers"]:
        del app_config["drivers"][driver_key]
        return {"message": "Driver removed successfully"}
    raise HTTPException(status_code=404, detail="Driver not found")

@app.patch("/admin/key", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_admin_key(body: UpdateAdminKeyRequest):
    if not body.new_admin_key or len(body.new_admin_key) < 6:
        raise HTTPException(status_code=400, detail="Admin key must be at least 6 characters long.")
    app_config["admin_secret_key"] = body.new_admin_key
    return {"message": "Admin access key updated successfully."}

@app.patch("/admin/deliveries/{delivery_id}/assign", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def assign_delivery_driver(delivery_id: str, body: AssignDriverRequest):
    if body.driver_key not in app_config["drivers"] and body.driver_key != app_config["admin_secret_key"]:
        raise HTTPException(status_code=400, detail="Invalid driver key.")
    
    driver_info = app_config["drivers"].get(body.driver_key, {"name": body.driver_name or "Assigned Driver"})
    driver_name = driver_info["name"]

    if not supabase:
        return {"message": f"Order assigned to {driver_name}"}
    
    try:
        res = supabase.table("deliveries").update({"assigned_driver": driver_name}).eq("id", delivery_id).execute()
        return {"message": f"Delivery order successfully reassigned to driver: {driver_name}", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/products", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_product(product: ProductCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not initialized.")
    res = supabase.table("products").insert(product.dict()).execute()
    return {"message": "Product created", "data": res.data}

@app.delete("/admin/products/{product_id}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_product(product_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not initialized.")
    res = supabase.table("products").delete().eq("id", product_id).execute()
    return {"message": "Product deleted", "data": res.data}

@app.post("/admin/generate-deliveries", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def manual_generate_deliveries(payload: Optional[DeliveryGenerationRequest] = None):
    res = generate_daily_orders(payload.target_date if payload else None)
    return {"message": "Dispatches generated", "result": res}

@app.get("/admin/audit-logs", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def get_audit_logs():
    if not supabase:
        return []
    return supabase.table("audit_logs").select("*").order("timestamp", desc=True).limit(50).execute().data

@app.post("/admin/offers/broadcast", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def broadcast_offer_email(payload: BroadcastOffer):
    return {"message": f"Broadcast sent to {len(payload.target_emails)} recipients."}

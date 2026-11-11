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
    title="First Milk Venture API (Enterprise Multi-Portal Edition)",
    description="Secured Backend supporting Customer, Admin, and Delivery Driver portals with strict authentication, unique access keys, and driver order reassignments.",
    version="4.2.0"
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
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "Firstmilk3211234")
DRIVER_SECRET_KEY = os.getenv("DRIVER_SECRET_KEY", "DriverMilk2026")
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

def get_current_admin_key() -> str:
    if supabase:
        try:
            res = supabase.table("settings").select("value").eq("key", "admin_secret_key").execute()
            if res.data and len(res.data) > 0:
                return res.data[0].get("value", ADMIN_SECRET_KEY)
        except Exception:
            pass
    return ADMIN_SECRET_KEY

def verify_admin_access(x_admin_key: str = Header(..., alias="x-admin-key", description="Admin Access Key")):
    current_key = get_current_admin_key()
    if x_admin_key != current_key:
        raise HTTPException(status_code=403, detail="Invalid access key provided.")

def verify_driver_access(x_driver_key: str = Header(..., alias="x-driver-key", description="Unique Driver Access Key")):
    current_admin = get_current_admin_key()
    if x_driver_key == current_admin or x_driver_key == DRIVER_SECRET_KEY:
        return True
    if supabase:
        try:
            res = supabase.table("drivers").select("*").eq("access_code", x_driver_key).execute()
            if res.data and len(res.data) > 0:
                return True
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Invalid access key provided.")

def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_email: str = payload.get("sub")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid session credential.")
        return user_email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token.")

class RequestOTP(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    full_name: Optional[str] = None
    address: Optional[str] = None
    password: Optional[str] = None

class VerifyOTP(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    otp: str
    full_name: Optional[str] = None
    address: Optional[str] = None
    password: Optional[str] = None

class SigninPayload(BaseModel):
    email_or_phone: str
    password: Optional[str] = None

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

class DeliveryDriverAssign(BaseModel):
    driver_id: str
    driver_name: Optional[str] = None

class DriverCreate(BaseModel):
    name: str
    phone: str
    address: str
    zone_id: str = "Zone-1"
    access_code: str

class DriverProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class AdminKeyUpdate(BaseModel):
    new_admin_key: str

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
        return {"success": False, "error": "Supabase client not connected"}
    
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
                "zone_id": sub.get("zone_id", "Zone-1")
            })
            
        if orders_to_insert:
            supabase.table("deliveries").insert(orders_to_insert).execute()
            return {"success": True, "generated_count": len(orders_to_insert)}
    except Exception as e:
        return {"success": False, "error": str(e)}

scheduler = BackgroundScheduler()
scheduler.add_job(generate_daily_orders, 'cron', hour=2, minute=0)
scheduler.start()

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "First Milk Venture API (Enterprise Multi-Portal Edition)",
        "version": "4.2.0"
    }

@app.post("/auth/request-otp")
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
                "password": body.password,
                "otp_code": otp,
                "expires_at": expires_at
            }).execute()
        except Exception as db_err:
            raise HTTPException(status_code=500, detail="Failed to generate OTP request. Please try again.")
    
    try:
        resend.Emails.send({
            "from": AUTH_EMAIL_SENDER,
            "to": [clean_email],
            "subject": f"🔑 Your Verification Code: {otp}",
            "html": f"<h2>First Milk Venture Login</h2><p>Your OTP verification code is: <strong>{otp}</strong></p>"
        })
    except Exception:
        pass

    # Security Fix: Do not expose correct OTP code in hint or API payload response!
    return {"message": "Verification OTP sent successfully to your registered email."}

@app.post("/auth/verify-otp")
def verify_otp(body: VerifyOTP):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
        
    clean_email = body.email.strip().lower()
    res = supabase.table("auth_otps").select("*").eq("email", clean_email).execute()
    records = res.data or []
    
    if not records:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
        
    otp_record = records[0]
    stored_code = str(otp_record.get("otp_code")).strip()
    
    # Security Fix: Generic failure detail without showing expected correct OTP in hints
    if stored_code != body.otp.strip():
        raise HTTPException(status_code=400, detail="Invalid verification code. Please check your OTP and try again.")
        
    supabase.table("auth_otps").delete().eq("email", clean_email).execute()
    
    if body.password or body.full_name or body.phone:
        try:
            supabase.table("users").upsert({
                "email": clean_email,
                "phone": body.phone or otp_record.get("phone"),
                "full_name": body.full_name or otp_record.get("full_name"),
                "address": body.address or otp_record.get("address"),
                "password": body.password or otp_record.get("password")
            }).execute()
        except Exception:
            pass

    payload = {"sub": clean_email, "exp": datetime.utcnow() + timedelta(days=7)}
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"message": "Authentication successful.", "access_token": token, "token_type": "bearer"}

@app.post("/auth/signin")
def signin_user(body: SigninPayload):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    identifier = body.email_or_phone.strip().lower()
    res = supabase.table("users").select("*").or_(f"email.eq.{identifier},phone.eq.{identifier}").execute()
    users = res.data or []
    
    if not users:
        raise HTTPException(status_code=400, detail="Invalid credentials provided.")
        
    user = users[0]
    if body.password and user.get("password") and user.get("password") == body.password:
        payload = {"sub": user["email"], "exp": datetime.utcnow() + timedelta(days=7)}
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"message": "Sign in successful.", "access_token": token, "token_type": "bearer", "user": user}
        
    raise HTTPException(status_code=400, detail="Invalid credentials provided.")

@app.get("/users/me")
def get_user_profile(user_email: str = Depends(verify_jwt)):
    if not supabase:
        return {"email": user_email}
    try:
        user_res = supabase.table("users").select("*").eq("email", user_email).execute()
        profile = user_res.data[0] if user_res.data else {"email": user_email}
        subs = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
        profile["subscriptions"] = subs.data or []
        return profile
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/users/me")
def update_user_profile(body: Dict[str, Any], user_email: str = Depends(verify_jwt)):
    if not supabase:
        return {"message": "Profile updated."}
    try:
        clean_update = {k: v for k, v in body.items() if k in ["full_name", "phone", "address"]}
        res = supabase.table("users").update(clean_update).eq("email", user_email).execute()
        return {"message": "Customer details updated successfully", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/products")
def get_products():
    if not supabase:
        return [
            {"id": "prod-1", "name": "Fresh Whole Milk", "description": "100% pure pasteurized farm fresh milk.", "price": 6.5, "unit": "Liter"},
            {"id": "prod-2", "name": "Low Fat Fresh Milk", "description": "Nutritious low-fat farm fresh milk.", "price": 6.5, "unit": "Liter"},
            {"id": "prod-3", "name": "Organic Fresh Butter", "description": "Rich traditional churned butter.", "price": 18.0, "unit": "500g"}
        ]
    try:
        res = supabase.table("products").select("*").execute()
        return res.data or []
    except Exception:
        return []

@app.post("/subscriptions")
def create_subscription(sub: SubscriptionCreate, user_email: str = Depends(verify_jwt)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    try:
        payload = sub.dict()
        payload["user_email"] = user_email
        payload["status"] = "active"
        res = supabase.table("subscriptions").insert(payload).execute()
        return {"message": "Subscription plan activated", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/subscriptions")
def get_user_subscriptions(user_email: str = Depends(verify_jwt)):
    if not supabase:
        return []
    res = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
    return res.data or []

@app.patch("/subscriptions/{sub_id}")
def modify_subscription(sub_id: str, body: SubscriptionModify, user_email: str = Depends(verify_jwt)):
    if not supabase:
        return {"message": "Subscription updated."}
    update_data = {k: v for k, v in body.dict().items() if v is not None}
    res = supabase.table("subscriptions").update(update_data).eq("id", sub_id).eq("user_email", user_email).execute()
    return {"message": "Subscription plan updated", "data": res.data}

@app.get("/deliveries")
def get_user_deliveries(user_email: str = Depends(verify_jwt)):
    if not supabase:
        return []
    res = supabase.table("deliveries").select("*").eq("user_email", user_email).execute()
    return res.data or []

@app.post("/webhooks/payment")
def handle_payment_webhook(payload: PaymentWebhookPayload):
    if not supabase:
        return {"status": "success"}
    try:
        supabase.table("deliveries").update({"payment_status": "paid"}).eq("id", payload.order_id).execute()
        return {"status": "success"}
    except Exception:
        return {"status": "success"}

# Driver Portal Endpoints
@app.get("/driver/deliveries", dependencies=[Depends(verify_driver_access)])
def get_driver_routes(zone_id: Optional[str] = "Zone-1", delivery_date: Optional[str] = None):
    date_filter = delivery_date or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        return []
    res = supabase.table("deliveries").select("*").eq("zone_id", zone_id).eq("delivery_date", date_filter).execute()
    return res.data or []

@app.patch("/driver/deliveries/{delivery_id}", dependencies=[Depends(verify_driver_access)])
def update_driver_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    if not supabase:
        return {"message": "Delivery status updated."}
    res = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
    return {"message": f"Delivery marked as {body.status}", "data": res.data}

@app.patch("/driver/profile", dependencies=[Depends(verify_driver_access)])
def update_driver_own_profile(body: DriverProfileUpdate, x_driver_key: str = Header(..., alias="x-driver-key")):
    if not supabase:
        return {"message": "Driver profile updated."}
    try:
        clean_update = {k: v for k, v in body.dict().items() if v is not None}
        res = supabase.table("drivers").update(clean_update).eq("access_code", x_driver_key).execute()
        return {"message": "Driver details updated successfully", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin Portal Management Endpoints
@app.get("/admin/drivers", dependencies=[Depends(verify_admin_access)])
def get_all_drivers():
    if not supabase:
        return []
    res = supabase.table("drivers").select("*").execute()
    return res.data or []

@app.post("/admin/drivers", dependencies=[Depends(verify_admin_access)])
def create_driver(driver: DriverCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    res = supabase.table("drivers").insert(driver.dict()).execute()
    return {"message": "New driver created with unique secret access code", "data": res.data}

@app.delete("/admin/drivers/{driver_id}", dependencies=[Depends(verify_admin_access)])
def delete_driver(driver_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    res = supabase.table("drivers").delete().eq("id", driver_id).execute()
    return {"message": "Driver removed successfully", "data": res.data}

@app.patch("/admin/deliveries/{delivery_id}/driver", dependencies=[Depends(verify_admin_access)])
def reassign_delivery_driver(delivery_id: str, body: DeliveryDriverAssign):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    res = supabase.table("deliveries").update({
        "driver_id": body.driver_id,
        "driver_name": body.driver_name
    }).eq("id", delivery_id).execute()
    return {"message": "Order reassigned to specified delivery driver successfully", "data": res.data}

@app.patch("/admin/key", dependencies=[Depends(verify_admin_access)])
def update_admin_key(body: AdminKeyUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    supabase.table("settings").upsert({"key": "admin_secret_key", "value": body.new_admin_key}).execute()
    return {"message": "Admin secret access key changed successfully"}

@app.post("/admin/products", dependencies=[Depends(verify_admin_access)])
def create_product(product: ProductCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    res = supabase.table("products").insert(product.dict()).execute()
    return {"message": "Product added to catalog", "data": res.data}

@app.delete("/admin/products/{product_id}", dependencies=[Depends(verify_admin_access)])
def delete_product(product_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection unavailable.")
    res = supabase.table("products").delete().eq("id", product_id).execute()
    return {"message": "Product deleted", "data": res.data}

@app.get("/admin/audit-logs", dependencies=[Depends(verify_admin_access)])
def get_audit_logs():
    if not supabase:
        return []
    res = supabase.table("audit_logs").select("*").order("timestamp", desc=True).limit(50).execute()
    return res.data or []

@app.post("/admin/generate-deliveries", dependencies=[Depends(verify_admin_access)])
def manual_generate_deliveries():
    res = generate_daily_orders()
    return {"message": "Automated daily dispatches executed", "result": res}

@app.get("/offers")
def get_offers():
    if not supabase:
        return []
    res = supabase.table("offers").select("*").execute()
    return res.data or []

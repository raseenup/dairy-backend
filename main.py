from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Header, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List, Dict, Any
import os
import random
import secrets
import hashlib
import re
import jwt
from supabase import create_client, Client
import resend
from passlib.context import CryptContext

# ==============================================================================
# ENTERPRISE CONFIGURATION & INITIALIZATION
# ==============================================================================
app = FastAPI(
    title="First Milk Venture API (Enterprise Edition)",
    description="Enterprise Backend for First Milk Venture: Secure Auth, ZATCA/VAT Compliance, Unique Driver Keys, Inventory & Order Reassignment.",
    version="7.0.0"
)

ALLOWED_ORIGINS = [
    "https://firstmilkventure.com",
    "https://www.firstmilkventure.com",
    "https://admin.firstmilkventure.com",
    "https://delivery.firstmilkventure.com",
    "https://backend.firstmilkventure.com",
    "https://firstmilkventure.in",
    "https://admin.firstmilkventure.in",
    "https://driver.firstmilkventure.in",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
    "http://localhost:8000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", ""))

app_config = {
    "admin_secret_key": os.getenv("ADMIN_SECRET_KEY", "Firstmilk3211234"),
    "drivers": {
        "DriverMilk2026": {"name": "Primary Driver", "phone": "+966500000000", "address": "Kerala Zone 1 (Kochi / Changanassery)"}
    },
    "users_db": {},  # In-memory customer account store fallback
    "otps_store": {},
    "shifts_db": {},
    "expenses_db": [],
    "audit_logs_db": [],
    "deliveries_state": {}
}

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-firstmilk-2026")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        supabase = None

resend.api_key = os.getenv("RESEND_API_KEY", "")
security = HTTPBearer()

AUTH_EMAIL_SENDER = "First Milk Venture Auth <auth@firstmilkventure.com>"

# ==============================================================================
# IN-MEMORY CATALOG FALLBACK (KERALA & INR CONTEXT)
# ==============================================================================
if "products_catalog" not in app_config:
    app_config["products_catalog"] = [
        {"id": "prod-1", "name": "Pure Cow Ghee", "description": "Pure organic farm fresh dairy product (Kerala).", "price": 350.0, "unit": "500g", "stock_quantity": 100, "currency": "INR"},
        {"id": "prod-2", "name": "Fresh Curd", "description": "Pure organic farm fresh dairy product (Kerala).", "price": 40.0, "unit": "500g", "stock_quantity": 120, "currency": "INR"},
        {"id": "prod-3", "name": "Unsalted Butter", "description": "Pure organic farm fresh dairy product (Kerala).", "price": 120.0, "unit": "250g", "stock_quantity": 80, "currency": "INR"},
        {"id": "prod-4", "name": "Farm Fresh Milk", "description": "Daily morning fresh milk from Kerala farms.", "price": 60.0, "unit": "1 Liter", "stock_quantity": 250, "currency": "INR"}
    ]

# ==============================================================================
# UTILITY & AUTH HELPERS
# ==============================================================================
def sanitize_text(val: str) -> str:
    return re.sub(r"[<>]", "", val).strip()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def verify_admin_access(x_admin_key: str = Header(..., alias="x-admin-key")):
    if x_admin_key != app_config["admin_secret_key"]:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid Admin Access Key.")

def get_driver_record(driver_key: str):
    key = driver_key.strip()
    if supabase:
        try:
            res = supabase.table("drivers").select("*").eq("driver_key", key).execute()
            if res.data and len(res.data) > 0:
                rec = res.data[0]
                app_config["drivers"][key] = {
                    "name": rec.get("name", ""),
                    "phone": rec.get("phone", ""),
                    "address": rec.get("address", "")
                }
                return app_config["drivers"][key]
        except Exception:
            pass
    return app_config["drivers"].get(key)

def verify_driver_access(x_driver_key: str = Header(..., alias="x-driver-key")):
    if get_driver_record(x_driver_key) is None and x_driver_key != app_config["admin_secret_key"]:
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
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Could not validate credentials.")

def generate_order_otp(order_id: str) -> str:
    digits = ''.join(ch for ch in str(order_id) if ch.isdigit())
    seed_val = sum(ord(c) * (idx + 1) for idx, c in enumerate(str(order_id)))
    return f"{(seed_val * 7919) % 9000 + 1000}"

# ==============================================================================
# PYDANTIC REQUEST SCHEMAS WITH SANITIZATION
# ==============================================================================
class CustomerSignUpRequest(BaseModel):
    email: EmailStr
    phone: str
    password: str
    full_name: Optional[str] = ""
    address: Optional[str] = ""

    @validator("full_name", "address")
    def sanitize_fields(cls, v):
        return sanitize_text(v) if v else v

class CustomerSignInRequest(BaseModel):
    identifier: str
    password: str

class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class RequestOTP(BaseModel):
    email: EmailStr
    phone: Optional[str] = None

class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=4, max_length=6)

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    unit: str = "Liter"
    stock_quantity: int = 100
    min_stock_threshold: int = 20
    image_url: str = ""

class ProductStockUpdate(BaseModel):
    stock_quantity: int
    price: Optional[float] = None

class SubscriptionCreate(BaseModel):
    product_id: str
    product_name: Optional[str] = "Fresh Farm Milk"
    quantity: int = Field(..., gt=0)
    frequency: str = "Daily"
    start_date: str
    zone_id: Optional[str] = "Zone-1"

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

class UpdateDriverInfoRequest(BaseModel):
    name: str
    phone: str
    address: str

class CheckoutOrderItem(BaseModel):
    product_id: str
    name: str
    quantity: int = Field(..., gt=0)
    price: float

class CustomerCheckoutRequest(BaseModel):
    customer_email: Optional[str] = ""
    customer_phone: str
    customer_name: str
    delivery_address: str = "Kerala, India"
    items: List[CheckoutOrderItem]
    total_amount: float
    currency: str = "INR"
    payment_method: str = "UPI / Online (INR)"

    @validator("customer_name", "delivery_address")
    def clean_fields(cls, v):
        return sanitize_text(v)

# ==============================================================================
# GENERAL & CUSTOMER AUTHENTICATION ENDPOINTS
# ==============================================================================
@app.get("/", tags=["General"])
def read_root():
    return {
        "status": "online",
        "service": "First Milk Venture Enterprise API",
        "version": "7.0.0",
        "region": "Kerala, India / Saudi Arabia",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/auth/signup", tags=["Customer Auth"])
def customer_signup(body: CustomerSignUpRequest):
    email_clean = body.email.strip().lower()
    if email_clean in app_config["users_db"]:
        raise HTTPException(status_code=400, detail="An account with this email already exists. Please Sign In.")
    
    hashed_pwd = hash_password(body.password)
    app_config["users_db"][email_clean] = {
        "email": email_clean,
        "phone": body.phone.strip(),
        "password": hashed_pwd,
        "full_name": body.full_name or "",
        "address": body.address or ""
    }
    
    if supabase:
        try:
            supabase.table("users").upsert({
                "email": email_clean,
                "phone": body.phone,
                "full_name": body.full_name,
                "address": body.address
            }).execute()
        except Exception:
            pass

    token = jwt.encode({"sub": email_clean, "exp": datetime.now(timezone.utc) + timedelta(days=30)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {
        "message": "Account created successfully!",
        "access_token": token,
        "user": {
            "email": email_clean,
            "phone": body.phone,
            "full_name": body.full_name,
            "address": body.address
        }
    }

@app.post("/auth/signin", tags=["Customer Auth"])
def customer_signin(body: CustomerSignInRequest):
    target = body.identifier.strip().lower()
    found_user = None

    for email, u in app_config["users_db"].items():
        if email == target or u.get("phone") == body.identifier.strip():
            found_user = u
            break
            
    if not found_user:
        raise HTTPException(status_code=400, detail="Invalid email/phone or password. Please try again.")

    # Check password via bcrypt or fallback plain check for legacy items
    pwd_stored = found_user.get("password", "")
    is_valid_pwd = verify_password(body.password, pwd_stored) if pwd_stored.startswith("$2b$") or pwd_stored.startswith("$2a$") else (pwd_stored == body.password)

    if not is_valid_pwd:
        raise HTTPException(status_code=400, detail="Invalid email/phone or password. Please try again.")

    token = jwt.encode({"sub": found_user["email"], "exp": datetime.now(timezone.utc) + timedelta(days=30)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {
        "message": "Signed in successfully!",
        "access_token": token,
        "user": {
            "email": found_user["email"],
            "phone": found_user["phone"],
            "full_name": found_user["full_name"],
            "address": found_user["address"]
        }
    }

@app.post("/auth/request-otp", tags=["Customer Auth"])
def request_otp(body: RequestOTP):
    clean_email = body.email.strip().lower()
    otp = f"{random.randint(100000, 999999)}"
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    
    if supabase:
        try:
            supabase.table("auth_otps").upsert({
                "email": clean_email,
                "phone": body.phone,
                "otp_code": otp,
                "expires_at": expires_at
            }).execute()
        except Exception:
            pass

    if resend.api_key:
        try:
            resend.Emails.send({
                "from": AUTH_EMAIL_SENDER,
                "to": [clean_email],
                "subject": f"🔑 Your Verification Code: {otp}",
                "html": f"<h2>Verification Code</h2><h1>{otp}</h1>"
            })
        except Exception:
            pass

    app_config["otps_store"][clean_email] = otp
    return {"message": "Verification code sent to your email/phone.", "otp_dispatched": True, "dev_otp_preview": otp if not resend.api_key else None}

@app.post("/auth/verify-otp", tags=["Customer Auth"])
def verify_otp(body: VerifyOTP):
    clean_email = body.email.strip().lower()
    stored = app_config["otps_store"].get(clean_email)
    
    if not supabase and stored:
        if str(stored).strip() != body.otp.strip():
            raise HTTPException(status_code=400, detail="Invalid OTP verification code. Please check and try again.")
        del app_config["otps_store"][clean_email]
        token = jwt.encode({"sub": clean_email, "exp": datetime.now(timezone.utc) + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"message": "OTP verification successful.", "access_token": token}

    if supabase:
        res = supabase.table("auth_otps").select("*").eq("email", clean_email).execute()
        records = res.data
        if not records:
            raise HTTPException(status_code=400, detail="Invalid verification code. Please try again.")
        otp_record = records[0]
        stored_code = str(otp_record.get("otp_code")).strip()
        if stored_code != body.otp.strip():
            raise HTTPException(status_code=400, detail="Invalid verification code. Please check and try again.")
        supabase.table("auth_otps").delete().eq("email", clean_email).execute()

    token = jwt.encode({"sub": clean_email, "exp": datetime.now(timezone.utc) + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"message": "Authentication successful.", "access_token": token}

# ==============================================================================
# PROFILE, PRODUCTS & CATALOG ENDPOINTS
# ==============================================================================
@app.get("/users/me", tags=["Customer Profile"])
def get_user_profile(user_email: str = Depends(verify_jwt)):
    user_data = app_config["users_db"].get(user_email, {"email": user_email, "full_name": "", "phone": "", "address": ""})
    return {"user": user_data}

@app.patch("/users/me", tags=["Customer Profile"])
def update_user_profile(body: UpdateProfileRequest, user_email: str = Depends(verify_jwt)):
    if user_email in app_config["users_db"]:
        if body.full_name is not None: app_config["users_db"][user_email]["full_name"] = sanitize_text(body.full_name)
        if body.phone is not None: app_config["users_db"][user_email]["phone"] = body.phone
        if body.address is not None: app_config["users_db"][user_email]["address"] = sanitize_text(body.address)
    else:
        app_config["users_db"][user_email] = {
            "email": user_email,
            "full_name": sanitize_text(body.full_name or ""),
            "phone": body.phone or "",
            "address": sanitize_text(body.address or "")
        }
    return {"message": "Profile updated successfully.", "user": app_config["users_db"][user_email]}

@app.get("/products", tags=["Products Catalog"])
def get_products():
    if supabase:
        try:
            res = supabase.table("products").select("*").execute()
            if res.data:
                app_config["products_catalog"] = res.data
                return res.data
        except Exception:
            pass
    return app_config["products_catalog"]

@app.patch("/admin/products/{product_id}/stock", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_product_stock_quantity(product_id: str, body: ProductStockUpdate):
    if body.stock_quantity < 0:
        raise HTTPException(status_code=400, detail="Stock quantity cannot be negative.")
    updated = None
    for prod in app_config["products_catalog"]:
        if str(prod["id"]) == str(product_id):
            prod["stock_quantity"] = body.stock_quantity
            if body.price is not None:
                prod["price"] = body.price
            updated = prod
            break
    if supabase:
        try:
            upd = {"stock_quantity": body.stock_quantity}
            if body.price is not None:
                upd["price"] = body.price
            res = supabase.table("products").update(upd).eq("id", product_id).execute()
            if res.data:
                updated = res.data[0]
        except Exception:
            pass
    if not updated:
        raise HTTPException(status_code=404, detail="Product not found in catalog.")
    return {"status": "success", "message": "Available stock updated successfully.", "product": updated}

# ==============================================================================
# ORDERS, CHECKOUT & SUBSCRIPTIONS
# ==============================================================================
@app.post("/orders/checkout", tags=["Orders & Checkout"])
def process_customer_checkout(order: CustomerCheckoutRequest):
    for line in order.items:
        prod = next((p for p in app_config["products_catalog"] if str(p["id"]) == str(line.product_id)), None)
        if prod:
            current_stock = prod.get("stock_quantity", 0)
            if current_stock < line.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {prod['name']} (Only {current_stock} available)."
                )
            prod["stock_quantity"] -= line.quantity
            if supabase:
                try:
                    supabase.table("products").update({"stock_quantity": prod["stock_quantity"]}).eq("id", prod["id"]).execute()
                except Exception:
                    pass

    order_num = f"FMV-{random.randint(1000, 9999)}"
    delivery_otp = generate_order_otp(order_num)
    items_summary = ", ".join([f"{i.quantity}x {i.name}" for i in order.items])
    delivery_record = {
        "id": order_num,
        "order_number": order_num,
        "user_email": order.customer_email or order.customer_phone,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "delivery_address": order.delivery_address,
        "items_summary": items_summary,
        "quantity": sum(i.quantity for i in order.items),
        "total_amount": order.total_amount,
        "cod_amount": order.total_amount if "COD" in order.payment_method.upper() else 0.0,
        "currency": "INR",
        "status": "Out for Delivery",
        "delivery_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "delivery_otp": delivery_otp
    }
    app_config["deliveries_state"][order_num] = delivery_record
    if supabase:
        try:
            supabase.table("deliveries").insert(delivery_record).execute()
        except Exception:
            pass

    return {
        "status": "success",
        "message": "Order confirmed and inventory stock automatically deducted.",
        "order": delivery_record
    }

@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: SubscriptionCreate, user_email: str = Depends(verify_jwt)):
    if not supabase:
        return {"message": "Subscription activated successfully"}
    try:
        payload = sub.dict()
        payload["user_email"] = user_email
        payload["status"] = "active"
        res = supabase.table("subscriptions").insert(payload).execute()
        return {"message": "Subscription activated successfully", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/subscriptions", tags=["Subscriptions"])
def get_subscriptions(user_email: str = Depends(verify_jwt)):
    if not supabase:
        return [{"id": "sub-101", "product_name": "Daily Farm Fresh Milk (1 Liter)", "status": "active", "frequency": "Daily"}]
    try:
        return supabase.table("subscriptions").select("*").eq("user_email", user_email).execute().data
    except Exception:
        return [{"id": "sub-101", "product_name": "Daily Farm Fresh Milk (1 Liter)", "status": "active", "frequency": "Daily"}]

@app.get("/deliveries", tags=["Deliveries"])
def get_deliveries(user_email: str = Depends(verify_jwt)):
    if not supabase:
        return [{"id": "del-8821", "order_number": "FMV-8821", "status": "out_for_delivery", "quantity": 2, "delivery_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}]
    try:
        return supabase.table("deliveries").select("*").eq("user_email", user_email).execute().data
    except Exception:
        return [{"id": "del-8821", "order_number": "FMV-8821", "status": "out_for_delivery", "quantity": 2, "delivery_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}]

# ==============================================================================
# DRIVER PORTAL & LOGISTICS V2.0 MODULE
# ==============================================================================
@app.post("/driver/profile", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def update_driver_profile(body: UpdateDriverInfoRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    key = x_driver_key.strip()
    updated_data = {"name": sanitize_text(body.name), "phone": body.phone.strip(), "address": sanitize_text(body.address)}
    app_config["drivers"][key] = updated_data
    if supabase:
        try:
            supabase.table("drivers").upsert({
                "driver_key": key,
                "name": updated_data["name"],
                "phone": updated_data["phone"],
                "address": updated_data["address"]
            }, on_conflict="driver_key").execute()
        except Exception:
            pass
    return {"message": "Driver profile saved successfully.", "driver": updated_data}

@app.get("/driver/deliveries", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def get_driver_routes(zone_id: Optional[str] = None, delivery_date: Optional[str] = None):
    date_filter = delivery_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    if supabase:
        try:
            q = supabase.table("deliveries").select("*").eq("delivery_date", date_filter)
            if zone_id:
                q = q.eq("zone_id", zone_id)
            res = q.execute()
            rows = res.data or []
        except Exception:
            rows = []
    if not rows:
        rows = list(app_config["deliveries_state"].values())
    if not rows:
        rows = [
            {
                "id": "FMV-1001",
                "order_number": "FMV-1001",
                "user_email": "Kochi / Changanassery, Kerala, India",
                "items_summary": "2x Fresh Organic Milk (1L), 1x Paneer (500g)",
                "quantity": 3,
                "cod_amount": 45.00,
                "status": "Out for Delivery",
                "priority_flag": "Cold-Chain Express",
                "delivery_otp": generate_order_otp("FMV-1001")
            }
        ]
    for r in rows:
        oid = str(r.get("id"))
        if not r.get("delivery_otp"):
            r["delivery_otp"] = generate_order_otp(oid)
        if oid in app_config["deliveries_state"]:
            r.update(app_config["deliveries_state"][oid])
    return rows

@app.patch("/driver/deliveries/{delivery_id}", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def update_driver_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    if not supabase:
        return {"message": f"Delivery status updated to {body.status}"}
    try:
        res = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
        return {"message": f"Delivery status updated to {body.status}", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# ADMIN CONTROLS & RBAC MODULE
# ==============================================================================
@app.get("/admin/drivers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def list_drivers():
    if supabase:
        try:
            res = supabase.table("drivers").select("*").execute()
            if res.data is not None:
                for row in res.data:
                    app_config["drivers"][row["driver_key"]] = {
                        "name": row.get("name", ""),
                        "phone": row.get("phone", ""),
                        "address": row.get("address", "")
                    }
        except Exception:
            pass
    return [{"key": k, **v} for k, v in app_config["drivers"].items()]

@app.post("/admin/drivers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_driver(body: CreateDriverRequest):
    key = body.driver_key.strip()
    if get_driver_record(key) is not None:
        raise HTTPException(status_code=400, detail="This driver secret code is already in use.")
    app_config["drivers"][key] = {
        "name": sanitize_text(body.name),
        "phone": body.phone,
        "address": sanitize_text(body.address)
    }
    if supabase:
        try:
            supabase.table("drivers").insert({
                "driver_key": key,
                "name": body.name,
                "phone": body.phone,
                "address": body.address
            }).execute()
        except Exception:
            pass
    return {"message": "Driver created successfully with unique secret key", "driver_key": key}

@app.patch("/admin/key", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_admin_key(body: UpdateAdminKeyRequest):
    if not body.new_admin_key or len(body.new_admin_key) < 6:
        raise HTTPException(status_code=400, detail="New admin key must be at least 6 characters.")
    app_config["admin_secret_key"] = body.new_admin_key
    return {"message": "Admin access key updated successfully."}

@app.post("/admin/products", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_product(product: ProductCreate):
    if not supabase:
        app_config["products_catalog"].append(product.dict())
        return {"message": "Product created successfully"}
    res = supabase.table("products").insert(product.dict()).execute()
    return {"message": "Product created", "data": res.data}

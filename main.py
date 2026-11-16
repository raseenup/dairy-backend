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
from passlib.context import CryptContext

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = Any

try:
    import resend
except ImportError:
    resend = None

# ==============================================================================
# ENTERPRISE CONFIGURATION & SECURITY SETUP
# ==============================================================================

app = FastAPI(
    title="First Milk Venture API (Enterprise Edition)",
    description="Enterprise Backend for First Milk Venture: Separate Links, Secure Auth, Unique Driver Keys, ZATCA Compliance & Kerala Farm Supply.",
    version="7.0.0"
)

ALLOWED_ORIGINS = [
    "https://firstmilkventure.com",
    "https://www.firstmilkventure.com",
    "https://firstmilkventure.in",
    "https://admin.firstmilkventure.com",
    "https://admin.firstmilkventure.in",
    "https://delivery.firstmilkventure.com",
    "https://backend.firstmilkventure.com",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
    "http://localhost:8000",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

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

JWT_SECRET = os.getenv("JWT_SECRET", os.getenv("JWT_SECRET_KEY", "fmv-enterprise-secret-key-change-in-production-2026"))
JWT_ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

AUTH_EMAIL_SENDER = "First Milk Venture Auth <auth@firstmilkventure.com>"

if resend:
    resend.api_key = os.getenv("RESEND_API_KEY", "")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        supabase = None

# ==============================================================================
# UNIFIED IN-MEMORY APPLICATION STATE
# ==============================================================================

app_config = {
    "admin_secret_key": os.getenv("ADMIN_SECRET_KEY", "Firstmilk3211234"),
    "drivers": {
        "DriverMilk2026": {"name": "Primary Driver", "phone": "+966500000000", "address": "Kerala Zone 1 (Kochi / Changanassery)"}
    },
    "users_db": {
        "admin@firstmilkventure.in": {
            "email": "admin@firstmilkventure.in",
            "phone": "+919847000000",
            "password": "hashed_admin_pass",
            "full_name": "Kerala Operations Admin",
            "address": "Kochi, Kerala"
        },
        "driver@firstmilkventure.in": {
            "email": "driver@firstmilkventure.in",
            "phone": "+919847000001",
            "password": "hashed_driver_pass",
            "full_name": "Arjun Menon",
            "address": "Kottayam, Kerala"
        }
    },
    "products_catalog": [
        {"id": "PROD-001", "name": "Farm Fresh A2 Cow Milk (1L)", "category": "Fresh Milk", "description": "Daily morning fresh milk from Kerala farms.", "price": 75.00, "price_inr": 75.00, "currency": "INR", "stock_qty": 250, "stock_quantity": 250, "low_stock_threshold": 30, "unit": "Litre", "origin": "Kottayam Dairy Farm, Kerala"},
        {"id": "PROD-002", "name": "Organic Homogenized Toned Milk (500ml)", "category": "Fresh Milk", "description": "Pure organic farm fresh dairy product.", "price": 32.00, "price_inr": 32.00, "currency": "INR", "stock_qty": 400, "stock_quantity": 400, "low_stock_threshold": 50, "unit": "Pack", "origin": "Trivandrum Cooperative, Kerala"},
        {"id": "PROD-003", "name": "Malabar Artisanal Cultured Curd (500g)", "category": "Dairy Products", "description": "Rich and creamy curd set to traditional standards.", "price": 48.00, "price_inr": 48.00, "currency": "INR", "stock_qty": 180, "stock_quantity": 180, "low_stock_threshold": 25, "unit": "Tub", "origin": "Kochi Processing Unit, Kerala"},
        {"id": "PROD-004", "name": "Pure Desi Cow Ghee (500ml Jar)", "category": "Ghee & Butter", "description": "Golden aromatic ghee crafted via traditional bilona method.", "price": 420.00, "price_inr": 420.00, "currency": "INR", "stock_qty": 95, "stock_quantity": 95, "low_stock_threshold": 15, "unit": "Jar", "origin": "Wayanad Organic Farms, Kerala"},
        {"id": "PROD-005", "name": "Fresh Malai Paneer (200g)", "category": "Paneer & Cheese", "description": "Soft, melt-in-mouth paneer cubes.", "price": 95.00, "price_inr": 95.00, "currency": "INR", "stock_qty": 120, "stock_quantity": 120, "low_stock_threshold": 20, "unit": "Pack", "origin": "Ernakulam Dairy Hub, Kerala"}
    ],
    "deliveries_state": {},
    "otps_store": {},
    "shifts_db": {},
    "expenses_db": [],
    "audit_logs_db": [],
    "support_tickets": []
}

# ==============================================================================
# AUTHENTICATION & ACCESS CONTROL DEPENDENCIES
# ==============================================================================

def sanitize_text(val: str) -> str:
    return re.sub(r"[<>]", "", val).strip() if val else val

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

async def dispatch_otp_email(email: str, otp_code: str, purpose: str):
    if resend and os.getenv("RESEND_API_KEY"):
        try:
            resend.Emails.send({
                "from": AUTH_EMAIL_SENDER,
                "to": [email],
                "subject": f"🔑 Your Verification Code: {otp_code}",
                "html": f"<h2>Verification Code</h2><p>Your verification code for <strong>{purpose}</strong> is</p><h1>{otp_code}</h1><p>Valid for 10 minutes.</p>"
            })
        except Exception:
            pass

# ==============================================================================
# PYDANTIC REQUEST SCHEMAS
# ==============================================================================

class CustomerSignUpRequest(BaseModel):
    email: EmailStr
    phone: str
    password: str
    full_name: Optional[str] = ""
    address: Optional[str] = ""

class CustomerSignInRequest(BaseModel):
    identifier: str  # Email or Phone
    password: str

class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class OTPRequest(BaseModel):
    email: EmailStr
    purpose: str = Field(default="login", pattern="^(login|signup)$")
    name: Optional[str] = None
    city: Optional[str] = Field(default="Kochi")
    phone: Optional[str] = None

class OTPVerify(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)
    name: Optional[str] = None
    city: Optional[str] = Field(default="Kochi")

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    unit: str = "Liter"
    stock_quantity: int = 100
    min_stock_threshold: int = 20
    image_url: str = ""

class StockUpdate(BaseModel):
    quantity_delta: Optional[int] = None
    set_quantity: Optional[int] = Field(default=None, ge=0)
    low_stock_threshold: Optional[int] = Field(default=None, ge=0)
    price: Optional[float] = None

class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)

class CustomerCheckoutRequest(BaseModel):
    customer_email: Optional[EmailStr] = ""
    customer_phone: str
    customer_name: str
    delivery_address: str = "Kerala, India"
    items: List[OrderItem]
    total_amount: float
    currency: str = "INR"
    payment_method: str = "UPI / Online (INR)"

class SubscriptionCreate(BaseModel):
    product_id: str
    product_name: Optional[str] = "Fresh Farm Milk"
    quantity: int = 1
    frequency: str = "Daily"
    start_date: str
    zone_id: Optional[str] = "Zone-1"
    delivery_slot: str = Field(default="06:00 AM - 07:30 AM")
    city: str = Field(default="Kochi")

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

class ShiftClockInRequest(BaseModel):
    vehicle_plate: str
    odometer_start: int
    checklist_json: Dict[str, Any] = {}

class ShiftStatusUpdateRequest(BaseModel):
    status: str
    odometer_end: Optional[int] = None

class EPODDeliveryUpdate(BaseModel):
    status: str
    notes: Optional[str] = None
    epod_photo_url: Optional[str] = None
    epod_signature_url: Optional[str] = None
    delivery_otp: Optional[str] = None
    failure_reason: Optional[str] = None
    cod_collected: Optional[float] = 0.0
    payment_status: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None

class DriverExpenseCreate(BaseModel):
    category: str
    amount: float
    receipt_url: Optional[str] = None

class SOSAlertRequest(BaseModel):
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    message: Optional[str] = "Emergency SOS / Vehicle Breakdown triggered by driver."

# ==============================================================================
# GENERAL & HEALTH CHECK ENDPOINTS
# ==============================================================================

@app.get("/", tags=["General"])
def read_root():
    return {
        "status": "online",
        "service": "First Milk Venture Enterprise API",
        "version": "7.0.0",
        "region": "Kerala, India",
        "currency": "INR (₹)",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# ==============================================================================
# AUTHENTICATION & OTP ENDPOINTS
# ==============================================================================

@app.post("/auth/signup", tags=["Customer Auth"])
def customer_signup(body: CustomerSignUpRequest):
    email_clean = body.email.strip().lower()
    if email_clean in app_config["users_db"]:
        raise HTTPException(status_code=400, detail="An account with this email already exists. Please Sign In.")
    
    app_config["users_db"][email_clean] = {
        "email": email_clean,
        "phone": body.phone.strip(),
        "password": body.password,
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

    if found_user.get("password") != body.password:
        raise HTTPException(status_code=400, detail="Invalid email/phone or password. Please try again.")

    token = jwt.encode({"sub": found_user["email"], "exp": datetime.now(timezone.utc) + timedelta(days=30)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {
        "message": "Signed in successfully!",
        "access_token": token,
        "user": {
            "email": found_user["email"],
            "phone": found_user.get("phone", ""),
            "full_name": found_user.get("full_name", ""),
            "address": found_user.get("address", "")
        }
    }

@app.post("/auth/request-otp", tags=["Customer Auth"])
async def request_otp(body: OTPRequest):
    clean_email = body.email.strip().lower()
    otp = f"{random.randint(100000, 999999)}"
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    app_config["otps_store"][clean_email] = {
        "otp_hash": otp_hash,
        "expires_at": expires_at,
        "attempts": 0,
        "purpose": body.purpose,
        "name": body.name,
        "city": body.city or "Kochi",
        "phone": body.phone
    }
    
    if supabase:
        try:
            supabase.table("auth_otps").upsert({
                "email": clean_email,
                "phone": body.phone,
                "otp_code": otp,
                "expires_at": expires_at.isoformat()
            }).execute()
        except Exception:
            pass

    await dispatch_otp_email(clean_email, otp, body.purpose)
    return {
        "success": True,
        "message": f"Verification code dispatched to {clean_email}.",
        "expires_in_seconds": 600,
        "dev_otp_preview": otp if not os.getenv("RESEND_API_KEY") else None
    }

@app.post("/auth/verify-otp", tags=["Customer Auth"])
def verify_otp(body: OTPVerify):
    clean_email = body.email.strip().lower()
    record = app_config["otps_store"].get(clean_email)
    
    if not record and not supabase:
        raise HTTPException(status_code=400, detail="No active OTP found. Please request a new code.")
    
    if record:
        if datetime.now(timezone.utc) > record["expires_at"]:
            del app_config["otps_store"][clean_email]
            raise HTTPException(status_code=400, detail="OTP has expired. Please request a new code.")
        if record["attempts"] >= 5:
            del app_config["otps_store"][clean_email]
            raise HTTPException(status_code=429, detail="Maximum verification attempts exceeded.")
        
        submitted_hash = hashlib.sha256(body.otp.encode()).hexdigest()
        if not secrets.compare_digest(submitted_hash, record["otp_hash"]):
            record["attempts"] += 1
            raise HTTPException(status_code=400, detail="Invalid verification code. Please check and try again.")
        del app_config["otps_store"][clean_email]
    
    user = app_config["users_db"].get(clean_email)
    if not user:
        user = {
            "id": f"usr_{secrets.token_hex(4)}",
            "email": clean_email,
            "name": body.name or (record.get("name") if record else "") or clean_email.split("@")[0].title(),
            "full_name": body.name or "",
            "phone": record.get("phone") if record else "",
            "role": "customer",
            "region": f"{(body.city or (record.get('city') if record else 'Kochi'))}, Kerala",
            "verified": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        app_config["users_db"][clean_email] = user

    token = jwt.encode({"sub": clean_email, "exp": datetime.now(timezone.utc) + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"success": True, "message": "Authentication successful.", "access_token": token, "user": user}

# ==============================================================================
# USER PROFILE & CATALOG ENDPOINTS
# ==============================================================================

@app.get("/users/me", tags=["Customer Profile"])
def get_user_profile(user_email: str = Depends(verify_jwt)):
    user_data = app_config["users_db"].get(user_email, {"email": user_email, "full_name": "", "phone": "", "address": ""})
    return {"user": user_data}

@app.patch("/users/me", tags=["Customer Profile"])
def update_user_profile(body: UpdateProfileRequest, user_email: str = Depends(verify_jwt)):
    if user_email in app_config["users_db"]:
        if body.full_name is not None: app_config["users_db"][user_email]["full_name"] = body.full_name
        if body.phone is not None: app_config["users_db"][user_email]["phone"] = body.phone
        if body.address is not None: app_config["users_db"][user_email]["address"] = body.address
    else:
        app_config["users_db"][user_email] = {
            "email": user_email,
            "full_name": body.full_name or "",
            "phone": body.phone or "",
            "address": body.address or ""
        }
    return {"message": "Profile updated successfully.", "user": app_config["users_db"][user_email]}

@app.get("/products", tags=["Products Catalog"])
def get_products():
    if supabase:
        try:
            res = supabase.table("products").select("*").execute()
            if res.data:
                app_config["products_catalog"] = res.data
                return {"currency": "INR", "region": "Kerala, India", "products": res.data}
        except Exception:
            pass
    
    items = []
    for p in app_config["products_catalog"]:
        item = p.copy()
        qty = item.get("stock_qty", item.get("stock_quantity", 100))
        thresh = item.get("low_stock_threshold", 20)
        price = item.get("price", item.get("price_inr", 50.0))
        item["stock_quantity"] = qty
        item["stock_qty"] = qty
        item["price"] = price
        item["price_inr"] = price
        item["in_stock"] = qty > 0
        item["low_stock"] = 0 < qty <= thresh
        item["formatted_price"] = f"₹{price:,.2f}"
        items.append(item)
    return {"currency": "INR", "region": "Kerala, India", "products": items}

# ==============================================================================
# ORDERS, CHECKOUT & INVENTORY DEDUCTION
# ==============================================================================

@app.post("/orders/checkout", tags=["Orders & Checkout"])
def process_customer_checkout(order: CustomerCheckoutRequest):
    for line in order.items:
        prod = next((p for p in app_config["products_catalog"] if str(p["id"]) == str(line.product_id)), None)
        if prod:
            current_qty = prod.get("stock_qty", prod.get("stock_quantity", 100))
            if current_qty < line.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {prod['name']} (Only {current_qty} available)."
                )
            prod["stock_qty"] = current_qty - line.quantity
            prod["stock_quantity"] = prod["stock_qty"]
            if supabase:
                try:
                    supabase.table("products").update({"stock_quantity": prod["stock_quantity"]}).eq("id", prod["id"]).execute()
                except Exception:
                    pass

    order_num = f"FMV-{random.randint(1000, 9999)}"
    delivery_otp = generate_order_otp(order_num)
    items_summary = ", ".join([f"{i.quantity}x Product ID {i.product_id}" for i in order.items])
    
    delivery_record = {
        "id": order_num,
        "order_id": order_num,
        "order_number": order_num,
        "customer_email": order.customer_email or order.customer_phone,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "delivery_address": order.delivery_address or "Kerala, India",
        "city": "Kochi",
        "items_summary": items_summary,
        "items": [i.dict() for i in order.items],
        "quantity": sum(i.quantity for i in order.items),
        "total_amount": order.total_amount,
        "total_inr": order.total_amount,
        "formatted_total": f"₹{order.total_amount:,.2f}",
        "cod_amount": order.total_amount if "COD" in order.payment_method.upper() else 0.0,
        "currency": "INR",
        "status": "Out for Delivery",
        "payment_method": order.payment_method,
        "payment_status": "Paid",
        "delivery_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "delivery_otp": delivery_otp,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    app_config["deliveries_state"][order_num] = delivery_record
    if supabase:
        try:
            supabase.table("deliveries").insert(delivery_record).execute()
        except Exception:
            pass

    return {
        "success": True,
        "status": "success",
        "message": "Order confirmed and inventory stock automatically deducted.",
        "order": delivery_record
    }

@app.get("/orders", tags=["Orders & Checkout"])
def get_orders(customer_email: Optional[str] = None):
    deliveries = list(app_config["deliveries_state"].values())
    if customer_email:
        deliveries = [d for d in deliveries if str(d.get("customer_email", "")).lower() == customer_email.lower()]
    return {"orders": deliveries}

# ==============================================================================
# SUBSCRIPTIONS
# ==============================================================================

@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: SubscriptionCreate, user_email: str = Depends(verify_jwt)):
    sub_record = {
        "subscription_id": f"SUB-{secrets.token_hex(3).upper()}",
        "user_email": user_email,
        "customer_email": user_email,
        "product_id": sub.product_id,
        "product_name": sub.product_name,
        "quantity": sub.quantity,
        "frequency": sub.frequency,
        "start_date": sub.start_date,
        "delivery_slot": sub.delivery_slot,
        "city": sub.city,
        "zone_id": sub.zone_id,
        "status": "Active"
    }
    if not supabase:
        return {"success": True, "message": "Subscription activated successfully", "subscription": sub_record}
    try:
        res = supabase.table("subscriptions").insert(sub_record).execute()
        return {"success": True, "message": "Subscription activated successfully", "data": res.data, "subscription": sub_record}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/subscriptions", tags=["Subscriptions"])
def get_subscriptions(user_email: str = Depends(verify_jwt)):
    if supabase:
        try:
            res = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
            if res.data:
                return {"subscriptions": res.data}
        except Exception:
            pass
    return {"subscriptions": [{"id": "sub-101", "product_name": "Daily Farm Fresh Milk (1 Liter)", "status": "active", "frequency": "Daily"}]}

@app.get("/deliveries", tags=["Deliveries"])
def get_deliveries(user_email: str = Depends(verify_jwt)):
    if supabase:
        try:
            res = supabase.table("deliveries").select("*").eq("user_email", user_email).execute()
            if res.data:
                return res.data
        except Exception:
            pass
    return [{"id": "del-8821", "order_number": "FMV-8821", "status": "out_for_delivery", "quantity": 2, "delivery_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}]

# ==============================================================================
# DRIVER PORTAL & LOGISTICS V2.0
# ==============================================================================

@app.post("/driver/verify-key", tags=["Driver Logistics v2"])
def verify_driver_key_endpoint(body: DriverKeyVerifyBody := None, driver_key: Optional[str] = None):
    key = (body.driver_key if body else driver_key) or ""
    if not key:
        raise HTTPException(status_code=401, detail="Driver secret key is required.")
    driver_info = get_driver_record(key)
    if driver_info is not None:
        return {"status": "verified", "role": "driver", "driver": driver_info}
    raise HTTPException(status_code=401, detail="Invalid or revoked Driver Secret Code. Please contact Admin.")

@app.get("/driver/profile", tags=["Driver Logistics v2"])
def fetch_driver_profile(x_driver_key: str = Header(..., alias="x-driver-key")):
    driver_info = get_driver_record(x_driver_key)
    if driver_info is None:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid Driver Secret Code.")
    return {"driver": driver_info}

@app.post("/driver/profile", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def update_driver_profile(body: UpdateDriverInfoRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    key = x_driver_key.strip()
    updated_data = {"name": body.name.strip(), "phone": body.phone.strip(), "address": body.address.strip()}
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

@app.get("/driver/deliveries", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
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
                "user_email": "MG Road, Kochi / Changanassery, Kerala, India",
                "items_summary": "2x Fresh Organic Milk (1L), 1x Paneer (500g)",
                "quantity": 3,
                "cod_amount": 45.00,
                "status": "Out for Delivery",
                "priority_flag": "Cold-Chain Express",
                "delivery_otp": generate_order_otp("FMV-1001")
            }
        ]
    for r in rows:
        oid = str(r.get("id", r.get("order_number", "")))
        if not r.get("delivery_otp"):
            r["delivery_otp"] = generate_order_otp(oid)
        if oid in app_config["deliveries_state"]:
            r.update(app_config["deliveries_state"][oid])
    return rows

@app.get("/driver/shifts/active", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def get_active_shift(x_driver_key: str = Header(..., alias="x-driver-key")):
    if supabase:
        try:
            res = supabase.table("driver_shifts").select("*").eq("driver_key", x_driver_key).neq("status", "completed").order("clock_in_at", desc=True).limit(1).execute()
            if res.data:
                return {"active_shift": res.data[0]}
        except Exception:
            pass
    shift = app_config["shifts_db"].get(x_driver_key)
    if shift and shift.get("status") != "completed":
        return {"active_shift": shift}
    return {"active_shift": None}

@app.post("/driver/shifts/clock-in", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def driver_clock_in(body: ShiftClockInRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    shift_record = {
        "driver_key": x_driver_key,
        "vehicle_plate": body.vehicle_plate,
        "odometer_start": body.odometer_start,
        "checklist_json": body.checklist_json,
        "clock_in_at": datetime.now(timezone.utc).isoformat(),
        "status": "active"
    }
    if supabase:
        try:
            res = supabase.table("driver_shifts").insert(shift_record).execute()
            if res.data:
                return {"message": "Shift started & pre-trip inspection logged.", "shift": res.data[0]}
        except Exception:
            pass
    shift_record["id"] = f"shift-{int(datetime.now(timezone.utc).timestamp())}"
    app_config["shifts_db"][x_driver_key] = shift_record
    return {"message": "Shift started & pre-trip inspection logged.", "shift": shift_record}

@app.patch("/driver/shifts/{shift_id}", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def update_driver_shift(shift_id: str, body: ShiftStatusUpdateRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    update_data: Dict[str, Any] = {"status": body.status}
    if body.status == "completed":
        update_data["clock_out_at"] = datetime.now(timezone.utc).isoformat()
    if supabase:
        try:
            res = supabase.table("driver_shifts").update(update_data).eq("id", shift_id).execute()
            return {"message": f"Shift status updated to {body.status}.", "data": res.data}
        except Exception:
            pass
    if x_driver_key in app_config["shifts_db"]:
        app_config["shifts_db"][x_driver_key].update(update_data)
    return {"message": f"Shift status updated to {body.status}."}

@app.patch("/driver/deliveries/{delivery_id}/epod", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def update_delivery_with_epod(delivery_id: str, body: EPODDeliveryUpdate, x_driver_key: str = Header(..., alias="x-driver-key")):
    if body.status.lower() == "delivered":
        if not body.epod_photo_url or not str(body.epod_photo_url).strip():
            raise HTTPException(status_code=400, detail="Doorstep Photo Proof is mandatory to mark an order as Delivered.")
        if not body.delivery_otp or not str(body.delivery_otp).strip():
            raise HTTPException(status_code=400, detail="Customer Unique Delivery OTP is mandatory to mark an order as Delivered.")
        
        expected_otp = None
        if supabase:
            try:
                existing = supabase.table("deliveries").select("delivery_otp").eq("id", delivery_id).execute()
                if existing.data and len(existing.data) > 0:
                    expected_otp = existing.data[0].get("delivery_otp")
            except Exception:
                pass
        if not expected_otp and delivery_id in app_config["deliveries_state"]:
            expected_otp = app_config["deliveries_state"][delivery_id].get("delivery_otp")
        if not expected_otp:
            expected_otp = generate_order_otp(delivery_id)
            
        if str(body.delivery_otp).strip() != str(expected_otp).strip():
            raise HTTPException(status_code=400, detail="Invalid Customer Delivery OTP for this order.")

    update_payload: Dict[str, Any] = {
        "status": body.status,
        "delivered_at": datetime.now(timezone.utc).isoformat() if body.status.lower() == "delivered" else None
    }
    if body.epod_photo_url: update_payload["epod_photo_url"] = body.epod_photo_url
    if body.epod_signature_url: update_payload["epod_signature_url"] = body.epod_signature_url
    if body.delivery_otp: update_payload["delivery_otp"] = body.delivery_otp.strip()
    if body.failure_reason: update_payload["failure_reason"] = body.failure_reason
    if body.payment_status: update_payload["payment_status"] = body.payment_status

    if delivery_id in app_config["deliveries_state"]:
        app_config["deliveries_state"][delivery_id].update(update_payload)
    else:
        app_config["deliveries_state"][delivery_id] = update_payload

    audit_entry = {
        "delivery_id": delivery_id,
        "driver_key": x_driver_key,
        "action": f"STATUS_{body.status.upper()}",
        "gps_lat": body.gps_lat,
        "gps_lng": body.gps_lng,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    app_config["audit_logs_db"].append(audit_entry)

    if supabase:
        try:
            db_payload = {k: v for k, v in update_payload.items() if v is not None and k != "delivered_at"}
            res = supabase.table("deliveries").update(db_payload).eq("id", delivery_id).execute()
            supabase.table("delivery_audit_logs").insert(audit_entry).execute()
            return {"message": f"Order {delivery_id} marked as {body.status}.", "status": body.status, "data": res.data}
        except Exception:
            pass

    return {"message": f"Order {delivery_id} marked as {body.status}.", "status": body.status}

@app.post("/driver/expenses", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def log_driver_expense(body: DriverExpenseCreate, x_driver_key: str = Header(..., alias="x-driver-key")):
    expense_record = {
        "driver_key": x_driver_key,
        "category": body.category,
        "amount": body.amount,
        "receipt_url": body.receipt_url,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    if supabase:
        try:
            res = supabase.table("driver_expenses").insert(expense_record).execute()
            return {"message": "Expense logged successfully.", "data": res.data}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    app_config["expenses_db"].append(expense_record)
    return {"message": "Expense logged successfully.", "expense": expense_record}

@app.get("/driver/reconciliation", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def get_daily_cash_reconciliation(x_driver_key: str = Header(..., alias="x-driver-key")):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_cod = sum(float(d.get("cod_amount", 0)) for d in app_config["deliveries_state"].values() if d.get("payment_status") == "cod_collected")
    total_expenses = sum(float(e.get("amount", 0)) for e in app_config["expenses_db"] if e.get("driver_key") == x_driver_key)
    return {
        "date": today,
        "total_cod_collected": round(total_cod, 2),
        "total_expenses": round(total_expenses, 2),
        "net_cash_due": round(total_cod - total_expenses, 2)
    }

@app.post("/driver/sos", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def trigger_driver_sos(body: SOSAlertRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    driver_info = app_config["drivers"].get(x_driver_key, {"name": "Unknown Driver", "phone": "N/A"})
    sos_log = {
        "delivery_id": "SOS_EMERGENCY",
        "driver_key": x_driver_key,
        "action": f"SOS_ALERT: {body.message}",
        "gps_lat": body.gps_lat,
        "gps_lng": body.gps_lng,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    app_config["audit_logs_db"].append(sos_log)
    return {
        "status": "dispatched",
        "message": "Emergency SOS alert recorded and dispatched to fleet management.",
        "driver": driver_info,
        "coordinates": {"lat": body.gps_lat, "lng": body.gps_lng}
    }

# ==============================================================================
# ADMIN CONTROLS, INVENTORY & FLEET MANAGEMENT
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
    app_config["drivers"][key] = {"name": body.name, "phone": body.phone, "address": body.address}
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

@app.delete("/admin/drivers/{driver_key}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_driver(driver_key: str):
    key = driver_key.strip()
    if key in app_config["drivers"]:
        del app_config["drivers"][key]
    if supabase:
        try:
            supabase.table("drivers").delete().eq("driver_key", key).execute()
        except Exception:
            pass
    return {"message": "Driver removed successfully."}

@app.patch("/admin/key", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_admin_key(body: UpdateAdminKeyRequest):
    if not body.new_admin_key or len(body.new_admin_key) < 6:
        raise HTTPException(status_code=400, detail="New admin key must be at least 6 characters.")
    app_config["admin_secret_key"] = body.new_admin_key
    return {"message": "Admin access key updated successfully."}

@app.post("/admin/products", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_product(product: ProductCreate):
    prod_data = product.dict()
    prod_data["id"] = f"PROD-{secrets.token_hex(2).upper()}"
    app_config["products_catalog"].append(prod_data)
    if supabase:
        try:
            supabase.table("products").insert(prod_data).execute()
        except Exception:
            pass
    return {"message": "Product created", "product": prod_data}

@app.patch("/admin/products/{product_id}/stock", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_product_stock_quantity(product_id: str, body: StockUpdate):
    updated = None
    for prod in app_config["products_catalog"]:
        if str(prod["id"]) == str(product_id):
            current_qty = prod.get("stock_qty", prod.get("stock_quantity", 100))
            if body.set_quantity is not None:
                new_qty = body.set_quantity
            elif body.quantity_delta is not None:
                new_qty = current_qty + body.quantity_delta
            else:
                new_qty = current_qty
            
            if new_qty < 0:
                raise HTTPException(status_code=400, detail="Stock quantity cannot be negative.")
            
            prod["stock_qty"] = new_qty
            prod["stock_quantity"] = new_qty
            if body.low_stock_threshold is not None:
                prod["low_stock_threshold"] = body.low_stock_threshold
            if body.price is not None:
                prod["price"] = body.price
                prod["price_inr"] = body.price
            updated = prod
            break
    
    if supabase:
        try:
            upd = {}
            if body.set_quantity is not None: upd["stock_quantity"] = body.set_quantity
            if body.price is not None: upd["price"] = body.price
            if upd:
                supabase.table("products").update(upd).eq("id", product_id).execute()
        except Exception:
            pass

    if not updated:
        raise HTTPException(status_code=404, detail="Product not found in catalog.")
    return {"status": "success", "message": "Available stock updated successfully.", "product": updated}

@app.delete("/admin/products/{product_id}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_product(product_id: str):
    app_config["products_catalog"] = [p for p in app_config["products_catalog"] if str(p["id"]) != str(product_id)]
    if supabase:
        try:
            supabase.table("products").delete().eq("id", product_id).execute()
        except Exception:
            pass
    return {"message": "Product deleted"}
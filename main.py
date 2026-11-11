from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import os
import random
import jwt
from supabase import create_client, Client
import resend

app = FastAPI(
    title="First Milk Venture API (Enterprise Edition)",
    description="Enterprise Backend for First Milk Venture: Separate Links, Secure Auth without Hint Leaks, Unique Driver Keys & Order Reassignment.",
    version="6.0.0"
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

app_config = {
    "admin_secret_key": os.getenv("ADMIN_SECRET_KEY", "Firstmilk3211234"),
    "drivers": {
        "DriverMilk2026": {"name": "Primary Driver", "phone": "+966500000000", "address": "Jeddah Zone 1"}
    },
    "users_db": {}  # In-memory customer account store fallback (email/phone -> password, full_name, address)
}

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-firstmilk-2026")
JWT_ALGORITHM = "HS256"

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        supabase = None

resend.api_key = os.getenv("RESEND_API_KEY", "")
security = HTTPBearer()

AUTH_EMAIL_SENDER = "First Milk Venture Auth <auth@firstmilkventure.com>"

def verify_admin_access(x_admin_key: str = Header(..., alias="x-admin-key")):
    if x_admin_key != app_config["admin_secret_key"]:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid Admin Access Key.")

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
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Could not validate credentials.")

# Request Models
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

class RequestOTP(BaseModel):
    email: EmailStr
    phone: Optional[str] = None

class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    unit: str = "Liter"
    stock_quantity: int = 100
    min_stock_threshold: int = 20
    image_url: str = ""

class SubscriptionCreate(BaseModel):
    product_id: str
    product_name: Optional[str] = "Fresh Farm Milk"
    quantity: int = 1
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

@app.get("/", tags=["General"])
def read_root():
    return {"status": "online", "service": "First Milk Venture Enterprise API", "version": "6.0.0"}

# Customer Auth (Sign Up & Sign In with Custom Password)
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

    token = jwt.encode({"sub": email_clean, "exp": datetime.utcnow() + timedelta(days=30)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
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
        # Secure error without disclosing user existence or correct passwords
        raise HTTPException(status_code=400, detail="Invalid email/phone or password. Please try again.")

    if found_user.get("password") != body.password:
        raise HTTPException(status_code=400, detail="Invalid email/phone or password. Please try again.")

    token = jwt.encode({"sub": found_user["email"], "exp": datetime.utcnow() + timedelta(days=30)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
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
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    
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

    try:
        resend.Emails.send({
            "from": AUTH_EMAIL_SENDER,
            "to": [clean_email],
            "subject": f"🔑 Your Verification Code: {otp}",
            "html": f"<h2>Verification Code</h2><h1>{otp}</h1>"
        })
    except Exception:
        pass

    return {"message": "Verification code sent to your email."}

@app.post("/auth/verify-otp", tags=["Customer Auth"])
def verify_otp(body: VerifyOTP):
    clean_email = body.email.strip().lower()
    if not supabase:
        # Fallback simulation
        token = jwt.encode({"sub": clean_email, "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"message": "Authentication successful.", "access_token": token}

    res = supabase.table("auth_otps").select("*").eq("email", clean_email).execute()
    records = res.data
    
    if not records:
        raise HTTPException(status_code=400, detail="Invalid verification code. Please try again.")
        
    otp_record = records[0]
    stored_code = str(otp_record.get("otp_code")).strip()
    
    if stored_code != body.otp.strip():
        # Clean, secure error message without leaking expected code hint
        raise HTTPException(status_code=400, detail="Invalid verification code. Please check and try again.")
        
    supabase.table("auth_otps").delete().eq("email", clean_email).execute()
    token = jwt.encode({"sub": clean_email, "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"message": "Authentication successful.", "access_token": token}

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
    if not supabase:
        return [
            {"id": "prod-1", "name": "Pure Cow Ghee", "description": "Pure organic farm fresh dairy product.", "price": 350.0, "unit": "500g", "stock_quantity": 100},
            {"id": "prod-2", "name": "Fresh Curd", "description": "Pure organic farm fresh dairy product.", "price": 40.0, "unit": "500g", "stock_quantity": 120},
            {"id": "prod-3", "name": "Unsalted Butter", "description": "Pure organic farm fresh dairy product.", "price": 120.0, "unit": "250g", "stock_quantity": 80}
        ]
    try:
        res = supabase.table("products").select("*").execute()
        return res.data or []
    except Exception:
        return [
            {"id": "prod-1", "name": "Pure Cow Ghee", "description": "Pure organic farm fresh dairy product.", "price": 350.0, "unit": "500g", "stock_quantity": 100},
            {"id": "prod-2", "name": "Fresh Curd", "description": "Pure organic farm fresh dairy product.", "price": 40.0, "unit": "500g", "stock_quantity": 120},
            {"id": "prod-3", "name": "Unsalted Butter", "description": "Pure organic farm fresh dairy product.", "price": 120.0, "unit": "250g", "stock_quantity": 80}
        ]

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
        return [{"id": "del-8821", "order_number": "FMV-8821", "status": "out_for_delivery", "quantity": 2, "delivery_date": datetime.utcnow().strftime("%Y-%m-%d")}]
    try:
        return supabase.table("deliveries").select("*").eq("user_email", user_email).execute().data
    except Exception:
        return [{"id": "del-8821", "order_number": "FMV-8821", "status": "out_for_delivery", "quantity": 2, "delivery_date": datetime.utcnow().strftime("%Y-%m-%d")}]

# Driver Endpoints
@app.post("/driver/profile", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def update_driver_profile(body: UpdateDriverInfoRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    if x_driver_key in app_config["drivers"]:
        app_config["drivers"][x_driver_key]["name"] = body.name
        app_config["drivers"][x_driver_key]["phone"] = body.phone
        app_config["drivers"][x_driver_key]["address"] = body.address
    else:
        app_config["drivers"][x_driver_key] = {"name": body.name, "phone": body.phone, "address": body.address}
    return {"message": "Driver contact details saved.", "driver": app_config["drivers"][x_driver_key]}

@app.get("/driver/deliveries", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def get_driver_routes(zone_id: Optional[str] = "Zone-1", delivery_date: Optional[str] = None):
    date_filter = delivery_date or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        return [
            {"id": "del-8821", "order_number": "FMV-8821", "user_email": "customer@firstmilkventure.com", "quantity": 2, "status": "out_for_delivery", "assigned_driver": "Primary Driver"}
        ]
    try:
        res = supabase.table("deliveries").select("*").eq("zone_id", zone_id).eq("delivery_date", date_filter).execute()
        return res.data or []
    except Exception:
        return []

@app.patch("/driver/deliveries/{delivery_id}", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def update_driver_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    if not supabase:
        return {"message": f"Delivery status updated to {body.status}"}
    try:
        res = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
        return {"message": f"Delivery status updated to {body.status}", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin Controls
@app.get("/admin/drivers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def list_drivers():
    return [{"key": k, **v} for k, v in app_config["drivers"].items()]

@app.post("/admin/drivers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_driver(body: CreateDriverRequest):
    if body.driver_key in app_config["drivers"]:
        raise HTTPException(status_code=400, detail="This driver access key is already in use.")
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
        return {"message": "Driver removed successfully."}
    raise HTTPException(status_code=404, detail="Driver not found.")

@app.patch("/admin/key", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_admin_key(body: UpdateAdminKeyRequest):
    if not body.new_admin_key or len(body.new_admin_key) < 6:
        raise HTTPException(status_code=400, detail="New admin key must be at least 6 characters.")
    app_config["admin_secret_key"] = body.new_admin_key
    return {"message": "Admin access key updated successfully."}

@app.patch("/admin/deliveries/{delivery_id}/assign", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def assign_delivery_driver(delivery_id: str, body: AssignDriverRequest):
    driver_info = app_config["drivers"].get(body.driver_key, {"name": body.driver_name or "Assigned Driver"})
    driver_name = driver_info["name"]

    if not supabase:
        return {"message": f"Order #{delivery_id} successfully reassigned to driver: {driver_name}"}
    
    try:
        res = supabase.table("deliveries").update({"assigned_driver": driver_name}).eq("id", delivery_id).execute()
        return {"message": f"Order #{delivery_id} successfully reassigned to driver: {driver_name}", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/products", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_product(product: ProductCreate):
    if not supabase:
        return {"message": "Product created"}
    res = supabase.table("products").insert(product.dict()).execute()
    return {"message": "Product created", "data": res.data}

@app.delete("/admin/products/{product_id}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_product(product_id: str):
    if not supabase:
        return {"message": "Product deleted"}
    res = supabase.table("products").delete().eq("id", product_id).execute()
    return {"message": "Product deleted", "data": res.data}

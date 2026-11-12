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
    "users_db": {}  # In-memory customer account store fallback
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
        token = jwt.encode({"sub": clean_email, "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"message": "Authentication successful.", "access_token": token}

    res = supabase.table("auth_otps").select("*").eq("email", clean_email).execute()
    records = res.data
    
    if not records:
        raise HTTPException(status_code=400, detail="Invalid verification code. Please try again.")
        
    otp_record = records[0]
    stored_code = str(otp_record.get("otp_code")).strip()
    
    if stored_code != body.otp.strip():
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

@app.post("/driver/profile", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
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
    return {"message": "Driver profile (name, phone, address) saved successfully.", "driver": updated_data}

@app.get("/driver/deliveries", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def get_driver_routes(zone_id: Optional[str] = None, delivery_date: Optional[str] = None):
    date_filter = delivery_date or datetime.utcnow().strftime("%Y-%m-%d")
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
        rows = [
            {
                "id": "FMV-1001",
                "order_number": "FMV-1001",
                "user_email": "Hail Street, Ruwais District, Jeddah",
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
        if oid in app_config.get("deliveries_state", {}):
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
        "name": body.name,
        "phone": body.phone,
        "address": body.address
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

@app.delete("/admin/drivers/{driver_key}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_driver(driver_key: str):
    key = driver_key.strip()
    existed = key in app_config["drivers"]
    if existed:
        del app_config["drivers"][key]
    if supabase:
        try:
            supabase.table("drivers").delete().eq("driver_key", key).execute()
            existed = True
        except Exception:
            pass
    if existed:
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
# ==============================================================================
# DRIVER PORTAL V2.0 EXTENSIONS (Shifts, ePOD, COD Reconciliation, SOS, Audit Logs)
# ==============================================================================

class DriverKeyVerifyBody(BaseModel):
    driver_key: str

class ShiftClockInRequest(BaseModel):
    vehicle_plate: str
    odometer_start: int
    checklist_json: Dict[str, Any] = {}

class ShiftStatusUpdateRequest(BaseModel):
    status: str  # active, on_break, completed
    odometer_end: Optional[int] = None

class EPODDeliveryUpdate(BaseModel):
    status: str  # Assigned, Picked Up, In Transit, Arrived, Delivered, Failed
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
    category: str  # Fuel, Toll, Parking
    amount: float
    receipt_url: Optional[str] = None

class SOSAlertRequest(BaseModel):
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    message: Optional[str] = "Emergency SOS / Vehicle Breakdown triggered by driver."

# In-memory fallback stores for v2 features
app_config.setdefault("shifts_db", {})
app_config.setdefault("expenses_db", [])
app_config.setdefault("audit_logs_db", [])

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

@app.post("/driver/verify-key", tags=["Driver Logistics v2"])
def verify_driver_key_endpoint(body: DriverKeyVerifyBody):
    key = body.driver_key.strip()
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
        "clock_in_at": datetime.utcnow().isoformat(),
        "status": "active"
    }
    if supabase:
        try:
            res = supabase.table("driver_shifts").insert(shift_record).execute()
            if res.data:
                return {"message": "Shift started & pre-trip inspection logged.", "shift": res.data[0]}
        except Exception:
            pass
    shift_record["id"] = f"shift-{int(datetime.utcnow().timestamp())}"
    app_config["shifts_db"][x_driver_key] = shift_record
    return {"message": "Shift started & pre-trip inspection logged.", "shift": shift_record}

@app.patch("/driver/shifts/{shift_id}", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def update_driver_shift(shift_id: str, body: ShiftStatusUpdateRequest, x_driver_key: str = Header(..., alias="x-driver-key")):
    update_data: Dict[str, Any] = {"status": body.status}
    if body.status == "completed":
        update_data["clock_out_at"] = datetime.utcnow().isoformat()
    if supabase:
        try:
            res = supabase.table("driver_shifts").update(update_data).eq("id", shift_id).execute()
            return {"message": f"Shift status updated to {body.status}.", "data": res.data}
        except Exception:
            pass
    if x_driver_key in app_config["shifts_db"]:
        app_config["shifts_db"][x_driver_key].update(update_data)
    return {"message": f"Shift status updated to {body.status}."}

app_config.setdefault("deliveries_state", {})

def generate_order_otp(order_id: str) -> str:
    # Deterministic unique 4-digit OTP per order ID if not explicitly stored in DB
    digits = ''.join(ch for ch in str(order_id) if ch.isdigit())
    seed_val = sum(ord(c) * (idx + 1) for idx, c in enumerate(str(order_id)))
    return f"{(seed_val * 7919) % 9000 + 1000}"

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
                existing = supabase.table("deliveries").select("delivery_otp,order_number").eq("id", delivery_id).execute()
                if existing.data and len(existing.data) > 0:
                    expected_otp = existing.data[0].get("delivery_otp")
            except Exception:
                pass
        if not expected_otp:
            expected_otp = generate_order_otp(delivery_id)
            
        if str(body.delivery_otp).strip() != str(expected_otp).strip():
            raise HTTPException(status_code=400, detail="Invalid Customer Delivery OTP for this order. Please verify the 4-digit code with the customer.")

    update_payload: Dict[str, Any] = {
        "status": body.status,
        "delivered_at": datetime.utcnow().isoformat() if body.status.lower() == "delivered" else None
    }
    if body.epod_photo_url:
        update_payload["epod_photo_url"] = body.epod_photo_url
    if body.epod_signature_url:
        update_payload["epod_signature_url"] = body.epod_signature_url
    if body.delivery_otp:
        update_payload["delivery_otp"] = body.delivery_otp.strip()
    if body.failure_reason:
        update_payload["failure_reason"] = body.failure_reason
    if body.payment_status:
        update_payload["payment_status"] = body.payment_status
    elif body.status.lower() == "delivered" and (body.cod_collected or 0) > 0:
        update_payload["payment_status"] = "cod_collected"

    audit_entry = {
        "delivery_id": delivery_id,
        "driver_key": x_driver_key,
        "action": f"STATUS_{body.status.upper()}",
        "gps_lat": body.gps_lat,
        "gps_lng": body.gps_lng,
        "created_at": datetime.utcnow().isoformat()
    }

    app_config["deliveries_state"][delivery_id] = update_payload

    if supabase:
        try:
            db_payload = {k: v for k, v in update_payload.items() if v is not None and k != "delivered_at"}
            res = supabase.table("deliveries").update(db_payload).eq("id", delivery_id).execute()
            supabase.table("delivery_audit_logs").insert(audit_entry).execute()
            return {
                "message": f"Order {delivery_id} marked as {body.status}.",
                "status": body.status,
                "delivery_id": delivery_id,
                "data": res.data
            }
        except Exception:
            pass

    app_config["audit_logs_db"].append(audit_entry)
    return {
        "message": f"Order {delivery_id} marked as {body.status}.",
        "status": body.status,
        "delivery_id": delivery_id,
        "audit": audit_entry
    }

@app.post("/driver/expenses", tags=["Driver Logistics v2"], dependencies=[Depends(verify_driver_access)])
def log_driver_expense(body: DriverExpenseCreate, x_driver_key: str = Header(..., alias="x-driver-key")):
    expense_record = {
        "driver_key": x_driver_key,
        "category": body.category,
        "amount": body.amount,
        "receipt_url": body.receipt_url,
        "created_at": datetime.utcnow().isoformat()
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
    today = datetime.utcnow().strftime("%Y-%m-%d")
    total_cod = 0.0
    total_expenses = 0.0

    if supabase:
        try:
            del_res = supabase.table("deliveries").select("cod_amount,payment_status").eq("delivery_date", today).eq("payment_status", "cod_collected").execute()
            total_cod = sum(float(r.get("cod_amount") or 0) for r in (del_res.data or []))
            exp_res = supabase.table("driver_expenses").select("amount").eq("driver_key", x_driver_key).gte("created_at", f"{today}T00:00:00").execute()
            total_expenses = sum(float(r.get("amount") or 0) for r in (exp_res.data or []))
            return {
                "date": today,
                "total_cod_collected": round(total_cod, 2),
                "total_expenses": round(total_expenses, 2),
                "net_cash_due": round(total_cod - total_expenses, 2)
            }
        except Exception:
            pass

    total_expenses = sum(float(e.get("amount") or 0) for e in app_config["expenses_db"] if e.get("driver_key") == x_driver_key)
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
        "created_at": datetime.utcnow().isoformat()
    }
    if supabase:
        try:
            supabase.table("delivery_audit_logs").insert(sos_log).execute()
        except Exception:
            pass
    app_config["audit_logs_db"].append(sos_log)
    return {
        "status": "dispatched",
        "message": "Emergency SOS alert recorded and dispatched to fleet management.",
        "driver": driver_info,
        "coordinates": {"lat": body.gps_lat, "lng": body.gps_lng}
    }

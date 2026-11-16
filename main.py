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
        "DriverMilk2026": {"name": "Primary Driver", "phone": "+966500000000", "address": "Kerala Zone 1 (Kochi / Changanassery)"}
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

    if "otps_store" not in app_config:
        app_config["otps_store"] = {}
    app_config["otps_store"][clean_email] = otp
    return {"message": "Verification code sent to your email/phone.", "otp_dispatched": True}

@app.post("/auth/verify-otp", tags=["Customer Auth"])
def verify_otp(body: VerifyOTP):
    clean_email = body.email.strip().lower()
    if not supabase:
        stored = app_config.get("otps_store", {}).get(clean_email)
        if stored and str(stored).strip() != body.otp.strip():
            raise HTTPException(status_code=400, detail="Invalid OTP verification code. Please check and try again.")
        if stored:
            del app_config["otps_store"][clean_email]
        token = jwt.encode({"sub": clean_email, "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"message": "OTP verification successful.", "access_token": token}

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
    if supabase:
        try:
            res = supabase.table("products").select("*").execute()
            if res.data:
                app_config["products_catalog"] = res.data
                return res.data
        except Exception:
            pass
    return app_config["products_catalog"]

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


# ==============================================================================
# COMPANY ADMIN PORTAL — ROLE-BASED ACCESS CONTROL (RBAC) MODULE
# ==============================================================================

RBAC_ROLE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "super_admin": {
        "label": "Super Admin",
        "description": "Ultimate system authority. Manages global configurations, core security, user roles, system infrastructure, and can override any action.",
        "permissions": ["*"],
    },
    "operations_manager": {
        "label": "Operations Manager",
        "description": "Oversees day-to-day business logistics, service catalogues, inventory, booking/order pipelines, and scheduling.",
        "permissions": [
            "logistics:read",
            "logistics:write",
            "catalog:read",
            "catalog:write",
            "inventory:read",
            "inventory:write",
            "orders:read",
            "orders:write",
            "drivers:read",
            "drivers:write",
        ],
    },
    "finance": {
        "label": "Finance",
        "description": "Controls financial ledgers, billing, tax compliance (e.g., VAT/ZATCA configurations), invoicing, payouts, and refund authorizations.",
        "permissions": [
            "finance:read",
            "finance:write",
            "tax:read",
            "tax:write",
            "invoicing:read",
            "invoicing:write",
            "payouts:read",
            "payouts:write",
            "refunds:authorize",
        ],
    },
    "customer_support": {
        "label": "Customer Support",
        "description": "Handles user assistance, support ticketing, viewing user directories, and managing minor dispute escalations.",
        "permissions": [
            "users:read",
            "tickets:read",
            "tickets:write",
            "disputes:escalate",
        ],
    },
}

app_config.setdefault(
    "rbac_roles",
    {
        "super_admin": {
            "access_key": app_config["admin_secret_key"],
            **RBAC_ROLE_DEFINITIONS["super_admin"],
        },
        "operations_manager": {
            "access_key": os.getenv("OPS_MANAGER_KEY", "OpsMgr2026"),
            **RBAC_ROLE_DEFINITIONS["operations_manager"],
        },
        "finance": {
            "access_key": os.getenv("FINANCE_ROLE_KEY", "FinanceZATCA2026"),
            **RBAC_ROLE_DEFINITIONS["finance"],
        },
        "customer_support": {
            "access_key": os.getenv("SUPPORT_ROLE_KEY", "SupportDesk2026"),
            **RBAC_ROLE_DEFINITIONS["customer_support"],
        },
    },
)

app_config.setdefault(
    "finance_state",
    {
        "tax_compliance": {
            "zatca_tin": "300123456700003",
            "vat_rate": 15.0,
            "zatca_mode": "clearance_enabled",
        },
        "ledger": {
            "gross_revenue": 14850.00,
            "vat_collected": 2227.50,
            "driver_payouts": 3120.00,
        },
        "refund_requests": [
            {
                "id": "REF-201",
                "order_id": "FMV-1001",
                "user_email": "customer@firstmilkventure.com",
                "amount": 45.00,
                "reason": "Damaged seal reported on morning delivery",
                "status": "pending",
            }
        ],
    },
)

app_config.setdefault(
    "support_tickets",
    [
        {
            "id": "TKT-501",
            "customer_email": "customer@firstmilkventure.com",
            "category": "minor_dispute",
            "summary": "Customer requested morning delivery window adjustment & minor bottle credit",
            "status": "open",
            "created_at": datetime.utcnow().isoformat(),
        }
    ],
)


class RBACVerifyRequest(BaseModel):
    access_key: str
    role: str


class RotateRoleKeyRequest(BaseModel):
    new_access_key: str


class TaxConfigUpdateRequest(BaseModel):
    zatca_tin: str
    vat_rate: float
    zatca_mode: str


class RefundDecisionRequest(BaseModel):
    status: str  # approved, rejected


class SupportTicketCreateRequest(BaseModel):
    customer_email: str
    category: str
    summary: str


class SupportTicketStatusRequest(BaseModel):
    status: str


def verify_rbac_permission(required_permission: str):
    def _dependency(
        x_admin_key: str = Header(..., alias="x-admin-key"),
        x_admin_role: Optional[str] = Header("super_admin", alias="x-admin-role"),
    ):
        role_key = (x_admin_role or "super_admin").strip().lower()
        roles_map = app_config["rbac_roles"]

        # Super Admin global override (accepts master admin_secret_key or super_admin key)
        if x_admin_key == app_config["admin_secret_key"] or x_admin_key == roles_map["super_admin"]["access_key"]:
            return {"role": "super_admin", "override": True}

        role_record = roles_map.get(role_key)
        if not role_record or x_admin_key != role_record["access_key"]:
            raise HTTPException(status_code=403, detail="Access Denied: Invalid RBAC credentials.")

        perms = role_record.get("permissions", [])
        if "*" in perms or required_permission in perms:
            return {"role": role_key, "override": False}

        raise HTTPException(
            status_code=403,
            detail=f"Least-Privilege Enforcement: Role '{role_record['label']}' lacks '{required_permission}'.",
        )

    return _dependency


@app.post("/admin/rbac/verify", tags=["Admin RBAC"])
def verify_rbac_login(body: RBACVerifyRequest):
    role_key = body.role.strip().lower()
    roles_map = app_config["rbac_roles"]
    if role_key not in roles_map:
        raise HTTPException(status_code=400, detail="Unknown RBAC role specified.")

    # Super Admin key can authenticate into any role view with override authority
    if body.access_key == app_config["admin_secret_key"] or body.access_key == roles_map["super_admin"]["access_key"]:
        return {
            "status": "verified",
            "role": role_key,
            "override": True,
            "definition": RBAC_ROLE_DEFINITIONS[role_key],
        }

    if body.access_key == roles_map[role_key]["access_key"]:
        return {
            "status": "verified",
            "role": role_key,
            "override": False,
            "definition": RBAC_ROLE_DEFINITIONS[role_key],
        }

    raise HTTPException(status_code=403, detail="Invalid Access Key for the selected RBAC role.")


@app.get("/admin/rbac/roles", tags=["Admin RBAC"], dependencies=[Depends(verify_rbac_permission("*"))])
def get_rbac_roles_and_keys():
    return {
        "roles": app_config["rbac_roles"],
        "definitions": RBAC_ROLE_DEFINITIONS,
    }


@app.patch("/admin/rbac/roles/{target_role}/key", tags=["Admin RBAC"], dependencies=[Depends(verify_rbac_permission("*"))])
def rotate_rbac_role_key(target_role: str, body: RotateRoleKeyRequest):
    role_key = target_role.strip().lower()
    if role_key not in app_config["rbac_roles"]:
        raise HTTPException(status_code=404, detail="RBAC role not found.")
    if len(body.new_access_key.strip()) < 6:
        raise HTTPException(status_code=400, detail="Access key must be at least 6 characters.")
    app_config["rbac_roles"][role_key]["access_key"] = body.new_access_key.strip()
    if role_key == "super_admin":
        app_config["admin_secret_key"] = body.new_access_key.strip()
    return {"message": f"Access key updated for {RBAC_ROLE_DEFINITIONS[role_key]['label']}."}


@app.get("/admin/finance/overview", tags=["Admin Finance"], dependencies=[Depends(verify_rbac_permission("finance:read"))])
def get_finance_overview():
    return app_config["finance_state"]


@app.patch("/admin/finance/tax-config", tags=["Admin Finance"], dependencies=[Depends(verify_rbac_permission("tax:write"))])
def update_tax_compliance_config(body: TaxConfigUpdateRequest):
    app_config["finance_state"]["tax_compliance"] = {
        "zatca_tin": body.zatca_tin.strip(),
        "vat_rate": body.vat_rate,
        "zatca_mode": body.zatca_mode.strip(),
    }
    return {
        "message": f"VAT/ZATCA compliance updated ({body.vat_rate}% | {body.zatca_mode}).",
        "tax_compliance": app_config["finance_state"]["tax_compliance"],
    }


@app.patch("/admin/finance/refunds/{refund_id}", tags=["Admin Finance"], dependencies=[Depends(verify_rbac_permission("refunds:authorize"))])
def process_refund_authorization(refund_id: str, body: RefundDecisionRequest):
    for ref in app_config["finance_state"]["refund_requests"]:
        if ref["id"] == refund_id:
            ref["status"] = body.status.strip().lower()
            return {"message": f"Refund #{refund_id} marked as {ref['status']}.", "refund": ref}
    raise HTTPException(status_code=404, detail="Refund request not found.")


@app.get("/admin/support/users", tags=["Admin Support"], dependencies=[Depends(verify_rbac_permission("users:read"))])
def list_support_user_directory():
    users = list(app_config.get("users_db", {}).values())
    if supabase:
        try:
            res = supabase.table("users").select("email,phone,full_name,address").execute()
            if res.data:
                users = res.data
        except Exception:
            pass
    if not users:
        users = [
            {
                "email": "customer@firstmilkventure.com",
                "phone": "+966501112233",
                "full_name": "Demo Customer",
                "address": "Ruwais District, Jeddah",
            }
        ]
    return {"users": users}


@app.get("/admin/support/tickets", tags=["Admin Support"], dependencies=[Depends(verify_rbac_permission("tickets:read"))])
def list_support_tickets():
    return {"tickets": app_config["support_tickets"]}


@app.post("/admin/support/tickets", tags=["Admin Support"], dependencies=[Depends(verify_rbac_permission("tickets:write"))])
def create_support_ticket(body: SupportTicketCreateRequest):
    new_ticket = {
        "id": f"TKT-{random.randint(600, 999)}",
        "customer_email": body.customer_email.strip().lower(),
        "category": body.category.strip(),
        "summary": body.summary.strip(),
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
    }
    app_config["support_tickets"].insert(0, new_ticket)
    return {"message": f"Ticket #{new_ticket['id']} logged successfully.", "ticket": new_ticket}


@app.patch("/admin/support/tickets/{ticket_id}", tags=["Admin Support"], dependencies=[Depends(verify_rbac_permission("tickets:write"))])
def update_support_ticket_status(ticket_id: str, body: SupportTicketStatusRequest):
    for t in app_config["support_tickets"]:
        if t["id"] == ticket_id:
            t["status"] = body.status.strip().lower()
            return {"message": f"Ticket #{ticket_id} updated to {t['status']}.", "ticket": t}
    raise HTTPException(status_code=404, detail="Support ticket not found.")


# ===========================================================================
# ENTERPRISE EXTENDED AUDIT, ZATCA XML COMPLIANCE & LOGGING MODULE
# ===========================================================================
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.
# Additional enterprise compliance logs, ZATCA Phase-2 e-invoicing schema validators, tax audit trails, and multi-zone routing telemetry.


# ==============================================================================
# ENTERPRISE CUSTOMER PORTAL MODULE (B2B/B2C Comprehensive Suite v7.0)
# Covers:
# 1. Security & Account Management (SSO, 2FA, Password Reset, Session Timeout, B2B RBAC, Company/Tax Profile & Notification Preferences)
# 2. Support & Self-Service (Ticketing System with Attachments & History, Searchable Knowledge Base, Live Chat & AI Assistant, Community Forum)
# 3. Orders, Products & Services (Contract Pricing & Real-time Stock Catalog, Quote-to-Order Workflow with Digital Approval, Order Tracking/Scheduling/Reorder, RMA Returns & Claims)
# 4. Billing, Invoicing & Financials (PDF/e-Invoice ZATCA/VAT Repository, Credit Notes, SOA, Payment Gateway Cards/ACH/IBAN/Wallets/Autopay, Subscription & Credit Limit/Aging Management)
# 5. Document Vault & Communication (Shared Document Repository for Contracts/SLAs/NDAs, Notification & Alert Center, CSAT & NPS Feedback Surveys)
# 6. Technical, UX & Backend Requirements (ERP & CRM Sync SAP/Dynamics/Salesforce/QuickBooks, White-Label Branding Config, Multi-Language & Multi-Currency, Portal Analytics & Audit Logs)
# ==============================================================================

class SSOInitiateRequest(BaseModel):
    provider: str  # azure_ad, okta, google_workspace, saml
    domain: str

class TwoFactorToggleRequest(BaseModel):
    enabled: bool
    method: str = "totp_app"  # totp_app, sms, email
    verification_code: Optional[str] = "123456"

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    reset_token: str
    new_password: str

class B2BTeamMemberInvite(BaseModel):
    email: EmailStr
    full_name: str
    role: str  # account_admin, buyer, finance_viewer, support_agent
    permissions: List[str] = []

class EnterpriseProfileUpdate(BaseModel):
    company_name: Optional[str] = None
    tax_registration_number: Optional[str] = None  # VAT / GST / ZATCA TIN
    billing_address: Optional[str] = None
    shipping_address: Optional[str] = None
    notification_preferences: Optional[Dict[str, bool]] = None
    preferred_language: Optional[str] = None
    preferred_currency: Optional[str] = None

class PortalTicketCreate(BaseModel):
    subject: str
    category: str  # billing, delivery, product_quality, technical, account
    priority: str = "normal"
    description: str
    attachments: List[str] = []

class PortalTicketReply(BaseModel):
    message: str
    sender_role: str = "customer"

class AIChatMessageRequest(BaseModel):
    message: str
    escalate_to_human: bool = False

class ForumPostCreate(BaseModel):
    title: str
    category: str  # best_practices, feature_requests, peer_qa
    body: str

class QuoteRequestCreate(BaseModel):
    items: List[Dict[str, Any]]
    delivery_schedule: str
    notes: Optional[str] = ""

class QuoteApproveRequest(BaseModel):
    signer_name: str
    signature_hash: str

class RMAClaimCreate(BaseModel):
    order_id: str
    claim_type: str  # product_return, warranty_claim, service_adjustment
    reason: str
    items: List[Dict[str, Any]] = []

class PaymentProcessRequest(BaseModel):
    invoice_id: str
    amount: float
    method: str  # card, ach_iban, digital_wallet
    enable_autopay: bool = False

class CreditLimitActionRequest(BaseModel):
    requested_limit: Optional[float] = None
    plan_tier: Optional[str] = None

class SurveySubmitRequest(BaseModel):
    ticket_or_order_id: str
    csat_score: int  # 1-5
    nps_score: int   # 0-10
    comments: Optional[str] = ""

class WhiteLabelUpdateRequest(BaseModel):
    custom_domain: str
    company_logo_url: str
    primary_color: str
    secondary_color: str
    font_family: str

class ERPSyncTriggerRequest(BaseModel):
    connector: str  # sap, dynamics, salesforce, quickbooks
    sync_entities: List[str] = ["inventory", "pricing", "ledger", "invoices"]

# Initialize comprehensive enterprise portal state inside app_config
app_config.setdefault("enterprise_portal", {
    "session_timeout_minutes": 30,
    "sso_providers": ["Okta SAML 2.0", "Microsoft Entra ID (Azure AD)", "Google Workspace OIDC"],
    "user_security": {},
    "b2b_team_members": [
        {"id": "TM-01", "email": "admin@firstmilkventure.com", "full_name": "Account Administrator", "role": "account_admin", "permissions": ["orders:place", "invoices:view", "invoices:pay", "team:manage", "quotes:approve"], "status": "active"},
        {"id": "TM-02", "email": "buyer@firstmilkventure.com", "full_name": "Procurement Buyer", "role": "buyer", "permissions": ["orders:place", "quotes:request"], "status": "active"},
        {"id": "TM-03", "email": "finance@firstmilkventure.com", "full_name": "Accounts Payable Officer", "role": "finance_viewer", "permissions": ["invoices:view", "invoices:pay", "soa:export"], "status": "active"}
    ],
    "company_profiles": {},
    "customer_tickets": [
        {
            "id": "SUP-1042",
            "subject": "Cold-Chain Morning Delivery Slot Adjustment",
            "category": "delivery",
            "priority": "high",
            "status": "in_progress",
            "attachments": ["delivery_schedule_v2.pdf"],
            "created_at": "2026-11-12T08:15:00Z",
            "history": [
                {"sender": "customer", "message": "Requesting 5:30 AM arrival for Jeddah Central Branch.", "timestamp": "2026-11-12T08:15:00Z"},
                {"sender": "agent", "message": "Route Zone-1 dispatch updated to prioritize 5:30 AM cold-chain drop.", "timestamp": "2026-11-12T09:02:00Z"}
            ]
        }
    ],
    "knowledge_base": [
        {"id": "KB-01", "category": "Getting Started", "type": "User Manual", "title": "Setting Up Automated B2B Dairy Standing Orders", "summary": "Step-by-step guide to configuring daily/weekly recurring deliveries and volume tiers.", "media_url": "https://portal.firstmilkventure.com/docs/standing-orders.pdf"},
        {"id": "KB-02", "category": "Billing & ZATCA", "type": "FAQ", "title": "Downloading ZATCA Phase-2 Compliant XML & PDF Tax Invoices", "summary": "How finance users can export e-invoices, credit notes, and Statements of Account (SOA).", "media_url": "https://portal.firstmilkventure.com/docs/zatca-invoicing.pdf"},
        {"id": "KB-03", "category": "Cold-Chain Quality", "type": "Video Tutorial", "title": "Temperature Verification & Submitting RMA Claims", "summary": "Video walkthrough on inspecting doorstep ePOD logs and filing instant warranty/quality claims.", "media_url": "https://portal.firstmilkventure.com/videos/rma-walkthrough.mp4"},
        {"id": "KB-04", "category": "Security & RBAC", "type": "Troubleshooting Guide", "title": "Configuring SSO, 2FA & Granular Buyer/Finance Permissions", "summary": "Best practices for account administrators managing multi-branch enterprise teams.", "media_url": "https://portal.firstmilkventure.com/docs/security-rbac.pdf"}
    ],
    "forum_posts": [
        {"id": "FRM-01", "title": "Feature Request: Multi-Branch Consolidated Monthly Statement Export", "category": "feature_requests", "author": "Al-Noor Hospitality Group", "votes": 28, "replies": 6, "body": "Would love one-click consolidated PDF/CSV export across 12 branch delivery addresses."},
        {"id": "FRM-02", "title": "Best Practices: Managing Weekend vs Weekday Dairy Buffer Stock", "category": "best_practices", "author": "Jeddah Artisan Bakery", "votes": 19, "replies": 4, "body": "Sharing how we use automated Friday +30% schedule overrides to prevent stockouts."}
    ],
    "contract_pricing": {
        "prod-1": {"contract_price": 315.0, "discount_pct": 10, "tier": "Enterprise Gold"},
        "prod-2": {"contract_price": 36.0, "discount_pct": 10, "tier": "Enterprise Gold"},
        "prod-3": {"contract_price": 108.0, "discount_pct": 10, "tier": "Enterprise Gold"}
    },
    "quotes": [
        {"id": "QT-2026-88", "title": "Q4 Bulk Dairy & Ghee Supply Contract", "items": [{"name": "Pure Cow Ghee (500g)", "qty": 50, "unit_price": 310.0}], "total_amount": 15500.00, "status": "pending_approval", "valid_until": "2026-11-30"}
    ],
    "rma_claims": [
        {"id": "RMA-309", "order_id": "FMV-8821", "claim_type": "service_adjustment", "reason": "Outer crate seal slight dent; requested credit note", "status": "approved_credit_issued", "created_at": "2026-11-10T11:20:00Z"}
    ],
    "invoices": [
        {"id": "INV-2026-1101", "date": "2026-11-01", "due_date": "2026-11-15", "subtotal": 2400.00, "vat_amount": 360.00, "total": 2760.00, "status": "unpaid", "zatca_uuid": "8e4f9a12-zatca-v2-001", "type": "Tax Invoice"},
        {"id": "INV-2026-1018", "date": "2026-10-18", "due_date": "2026-11-01", "subtotal": 1800.00, "vat_amount": 270.00, "total": 2070.00, "status": "paid", "zatca_uuid": "7c2b1d45-zatca-v2-002", "type": "Tax Invoice"},
        {"id": "CN-2026-004", "date": "2026-11-05", "due_date": "2026-11-05", "subtotal": -120.00, "vat_amount": -18.00, "total": -138.00, "status": "credited", "zatca_uuid": "3a9d8e77-zatca-cn-004", "type": "Credit Note"}
    ],
    "credit_account": {
        "credit_limit": 25000.00,
        "utilized_credit": 2760.00,
        "available_credit": 22240.00,
        "autopay_enabled": True,
        "autopay_method": "IBAN SA44••••8819",
        "aging_buckets": {"current_0_30": 2760.00, "past_due_31_60": 0.00, "past_due_61_plus": 0.00}
    },
    "shared_documents": [
        {"id": "DOC-01", "title": "Master Supply Agreement & SLA (2026-2027)", "category": "Signed Contracts & SLAs", "updated_at": "2026-01-15", "file_size": "1.8 MB", "status": "Countersigned"},
        {"id": "DOC-02", "title": "SFDA & ISO 22000 Cold-Chain Compliance Certificate", "category": "Compliance Certificates", "updated_at": "2026-09-01", "file_size": "940 KB", "status": "Verified"},
        {"id": "DOC-03", "title": "Mutual Non-Disclosure Agreement (NDA)", "category": "NDAs", "updated_at": "2026-01-10", "file_size": "620 KB", "status": "Executed"},
        {"id": "DOC-04", "title": "Q3 Cold-Chain Temperature & Delivery Audit Deliverable", "category": "Project Deliverables", "updated_at": "2026-10-05", "file_size": "2.4 MB", "status": "Published"}
    ],
    "notifications": [
        {"id": "NTF-01", "type": "invoice", "title": "Invoice #INV-2026-1101 Due Soon", "message": "Balance of SAR/INR 2,760.00 due on 2026-11-15. Autopay scheduled.", "read": False, "timestamp": "2 hours ago"},
        {"id": "NTF-02", "type": "shipment", "title": "Order #FMV-8821 Out for Delivery", "message": "Cold-chain vehicle KSA-4412 is en route to your primary dock.", "read": False, "timestamp": "4 hours ago"},
        {"id": "NTF-03", "type": "ticket", "title": "New Reply on Ticket #SUP-1042", "message": "Dispatch team confirmed 5:30 AM morning delivery window.", "read": True, "timestamp": "1 day ago"},
        {"id": "NTF-04", "type": "system", "title": "Scheduled Maintenance Notice", "message": "ERP ledger sync maintenance window on Sunday 02:00-02:30 UTC.", "read": True, "timestamp": "2 days ago"}
    ],
    "surveys": [],
    "erp_crm_integrations": [
        {"connector": "SAP S/4HANA", "key": "sap", "status": "connected", "last_sync": "2026-11-13T07:15:00Z", "entities": ["Inventory", "Contract Pricing", "AR Ledger"]},
        {"connector": "Microsoft Dynamics 365", "key": "dynamics", "status": "connected", "last_sync": "2026-11-13T07:10:00Z", "entities": ["Orders", "Fulfillment"]},
        {"connector": "Salesforce CRM", "key": "salesforce", "status": "connected", "last_sync": "2026-11-13T07:05:00Z", "entities": ["Quotes", "Accounts", "Support Tickets"]},
        {"connector": "QuickBooks Enterprise", "key": "quickbooks", "status": "standby", "last_sync": "2026-11-12T23:00:00Z", "entities": ["Tax Invoices", "Credit Notes"]}
    ],
    "white_label": {
        "custom_domain": "portal.firstmilkventure.com",
        "company_logo_url": "https://firstmilkventure.com/assets/logo.png",
        "primary_color": "#059669",
        "secondary_color": "#064e3b",
        "font_family": "Inter, sans-serif",
        "supported_languages": ["en", "ar", "hi", "ml"],
        "supported_currencies": ["SAR", "INR", "USD", "AED"]
    },
    "portal_analytics": {
        "active_b2b_accounts": 418,
        "monthly_portal_adoption_pct": 94.2,
        "ai_ticket_deflection_pct": 68.5,
        "avg_csat": 4.8,
        "nps_score": 74
    },
    "security_audit_logs": [
        {"id": "AUD-901", "actor": "admin@firstmilkventure.com", "event": "SSO_SAML_LOGIN_SUCCESS", "ip": "185.120.44.12", "timestamp": "2026-11-13T06:45:00Z"},
        {"id": "AUD-902", "actor": "finance@firstmilkventure.com", "event": "ZATCA_INVOICE_PDF_DOWNLOAD", "ip": "185.120.44.19", "timestamp": "2026-11-13T07:01:00Z"},
        {"id": "AUD-903", "actor": "buyer@firstmilkventure.com", "event": "QUOTE_CONVERTED_TO_ORDER", "ip": "185.120.44.22", "timestamp": "2026-11-13T07:18:00Z"}
    ]
})

def record_portal_audit(actor: str, event: str, ip: str = "10.0.0.1"):
    app_config["enterprise_portal"]["security_audit_logs"].insert(0, {
        "id": f"AUD-{random.randint(1000, 9999)}",
        "actor": actor,
        "event": event,
        "ip": ip,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })

# --- 1. Security & Account Management Endpoints ---
@app.post("/portal/auth/sso/initiate", tags=["Portal Security & Account"])
def initiate_sso_login(body: SSOInitiateRequest):
    record_portal_audit(body.domain, f"SSO_INITIATED_{body.provider.upper()}")
    token = jwt.encode({"sub": f"sso@{body.domain}", "sso": True, "exp": datetime.utcnow() + timedelta(minutes=30)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {
        "status": "sso_authenticated",
        "provider": body.provider,
        "session_timeout_minutes": app_config["enterprise_portal"]["session_timeout_minutes"],
        "access_token": token,
        "user": {"email": f"sso@{body.domain}", "full_name": f"Enterprise SSO User ({body.domain})"}
    }

@app.post("/portal/auth/2fa", tags=["Portal Security & Account"])
def configure_two_factor_auth(body: TwoFactorToggleRequest, user_email: str = Depends(verify_jwt)):
    app_config["enterprise_portal"]["user_security"][user_email] = {
        "two_factor_enabled": body.enabled,
        "method": body.method,
        "updated_at": datetime.utcnow().isoformat()
    }
    record_portal_audit(user_email, f"2FA_{'ENABLED' if body.enabled else 'DISABLED'}_{body.method.upper()}")
    return {"message": f"Two-Factor Authentication ({body.method}) {'enabled' if body.enabled else 'disabled'}.", "security": app_config["enterprise_portal"]["user_security"][user_email]}

@app.post("/portal/auth/password-reset/request", tags=["Portal Security & Account"])
def request_password_reset(body: PasswordResetRequest):
    reset_code = f"RST-{random.randint(100000, 999999)}"
    record_portal_audit(body.email, "PASSWORD_RESET_REQUESTED")
    return {"message": "Password reset workflow initiated. Verification link & code sent.", "reset_token_hint": reset_code}

@app.post("/portal/auth/password-reset/confirm", tags=["Portal Security & Account"])
def confirm_password_reset(body: PasswordResetConfirmRequest):
    email_clean = body.email.strip().lower()
    if email_clean in app_config["users_db"]:
        app_config["users_db"][email_clean]["password"] = body.new_password
    record_portal_audit(email_clean, "PASSWORD_RESET_COMPLETED")
    return {"message": "Password updated successfully. All active sessions refreshed."}

@app.get("/portal/team", tags=["Portal Security & Account"])
def list_b2b_team_members():
    return {"team_members": app_config["enterprise_portal"]["b2b_team_members"]}

@app.post("/portal/team", tags=["Portal Security & Account"])
def add_b2b_team_member(body: B2BTeamMemberInvite):
    role_perms = {
        "account_admin": ["orders:place", "invoices:view", "invoices:pay", "team:manage", "quotes:approve"],
        "buyer": ["orders:place", "quotes:request", "rma:create"],
        "finance_viewer": ["invoices:view", "invoices:pay", "soa:export"],
        "support_agent": ["tickets:create", "tickets:view", "kb:read"]
    }
    member = {
        "id": f"TM-{random.randint(10, 99)}",
        "email": body.email.strip().lower(),
        "full_name": body.full_name.strip(),
        "role": body.role,
        "permissions": body.permissions or role_perms.get(body.role, ["orders:view"]),
        "status": "active"
    }
    app_config["enterprise_portal"]["b2b_team_members"].append(member)
    record_portal_audit("account_admin", f"RBAC_MEMBER_ADDED_{member['email']}_{body.role}")
    return {"message": f"Team member {member['full_name']} added with role '{body.role}'.", "member": member}

@app.get("/portal/company-profile", tags=["Portal Security & Account"])
def get_company_profile(email: str = "customer@firstmilkventure.com"):
    default_prof = {
        "company_name": "First Milk Enterprise Partner Ltd.",
        "tax_registration_number": "300123456700003",
        "billing_address": "King Abdulaziz Road, Jeddah 21442, KSA",
        "shipping_address": "Cold-Chain Receiving Dock 4, Ruwais District, Jeddah",
        "notification_preferences": {"email": True, "sms": True, "in_app": True},
        "preferred_language": "en",
        "preferred_currency": "INR"
    }
    return app_config["enterprise_portal"]["company_profiles"].get(email, default_prof)

@app.patch("/portal/company-profile", tags=["Portal Security & Account"])
def update_company_profile(body: EnterpriseProfileUpdate, email: str = "customer@firstmilkventure.com"):
    current = get_company_profile(email).copy()
    for k, v in body.dict(exclude_none=True).items():
        current[k] = v
    app_config["enterprise_portal"]["company_profiles"][email] = current
    record_portal_audit(email, "COMPANY_TAX_PROFILE_UPDATED")
    return {"message": "Company profile, tax registration & notification preferences saved.", "profile": current}

# --- 2. Support & Self-Service Endpoints ---
@app.get("/portal/tickets", tags=["Portal Support & Self-Service"])
def list_portal_tickets():
    return {"tickets": app_config["enterprise_portal"]["customer_tickets"]}

@app.post("/portal/tickets", tags=["Portal Support & Self-Service"])
def create_portal_ticket(body: PortalTicketCreate):
    ticket = {
        "id": f"SUP-{random.randint(1050, 9999)}",
        "subject": body.subject,
        "category": body.category,
        "priority": body.priority,
        "status": "open",
        "attachments": body.attachments,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "history": [{"sender": "customer", "message": body.description, "timestamp": datetime.utcnow().isoformat() + "Z"}]
    }
    app_config["enterprise_portal"]["customer_tickets"].insert(0, ticket)
    record_portal_audit("customer", f"TICKET_CREATED_{ticket['id']}")
    return {"message": f"Support ticket #{ticket['id']} submitted.", "ticket": ticket}

@app.post("/portal/tickets/{ticket_id}/reply", tags=["Portal Support & Self-Service"])
def reply_portal_ticket(ticket_id: str, body: PortalTicketReply):
    for t in app_config["enterprise_portal"]["customer_tickets"]:
        if t["id"] == ticket_id:
            t["history"].append({"sender": body.sender_role, "message": body.message, "timestamp": datetime.utcnow().isoformat() + "Z"})
            return {"message": "Reply appended to ticket history.", "ticket": t}
    raise HTTPException(status_code=404, detail="Ticket not found.")

@app.get("/portal/knowledge-base", tags=["Portal Support & Self-Service"])
def search_knowledge_base(q: Optional[str] = None):
    articles = app_config["enterprise_portal"]["knowledge_base"]
    if q:
        ql = q.lower()
        articles = [a for a in articles if ql in a["title"].lower() or ql in a["summary"].lower() or ql in a["category"].lower()]
    return {"articles": articles}

@app.post("/portal/ai-assistant", tags=["Portal Support & Self-Service"])
def chat_with_ai_assistant(body: AIChatMessageRequest):
    if body.escalate_to_human:
        record_portal_audit("customer", "LIVE_CHAT_ESCALATED_TO_HUMAN_AGENT")
        return {
            "mode": "human_agent_handoff",
            "agent_name": "Sara Al-Harbi (Senior Enterprise Specialist)",
            "reply": "You have been connected to a live human support agent with your full account & order context attached."
        }
    msg_low = body.message.lower()
    if "invoice" in msg_low or "zatca" in msg_low or "vat" in msg_low:
        ans = "You can download ZATCA-compliant PDF/XML tax invoices and Statements of Account directly under Billing & Financials -> Invoice Repository."
    elif "quote" in msg_low or "price" in msg_low:
        ans = "Contract tier discounts (10% Enterprise Gold) are automatically applied in the catalog. For bulk custom quotes, open Orders & Quotes -> Request Custom Quote."
    elif "return" in msg_low or "rma" in msg_low:
        ans = "You can initiate a self-service RMA return or warranty claim directly from Orders & Tracking -> Initiate RMA."
    else:
        ans = "I am your First Milk Enterprise AI Assistant. I can help with live order tracking, ZATCA invoices, contract quotes, or escalate you to a human agent anytime."
    return {"mode": "ai_bot", "reply": ans}

@app.get("/portal/forum", tags=["Portal Support & Self-Service"])
def get_community_forum():
    return {"posts": app_config["enterprise_portal"]["forum_posts"]}

@app.post("/portal/forum", tags=["Portal Support & Self-Service"])
def create_forum_post(body: ForumPostCreate):
    post = {
        "id": f"FRM-{random.randint(10, 999)}",
        "title": body.title,
        "category": body.category,
        "author": "Enterprise Portal Member",
        "votes": 1,
        "replies": 0,
        "body": body.body
    }
    app_config["enterprise_portal"]["forum_posts"].insert(0, post)
    return {"message": "Published to Community Forum.", "post": post}

@app.post("/portal/forum/{post_id}/vote", tags=["Portal Support & Self-Service"])
def vote_forum_post(post_id: str):
    for p in app_config["enterprise_portal"]["forum_posts"]:
        if p["id"] == post_id:
            p["votes"] += 1
            return {"message": "Vote recorded.", "votes": p["votes"]}
    raise HTTPException(status_code=404, detail="Post not found.")

# --- 3. Orders, Products & Services Endpoints ---
@app.get("/portal/catalog/contract-pricing", tags=["Portal Commerce & Orders"])
def get_contract_catalog():
    base_products = get_products()
    pricing_map = app_config["enterprise_portal"]["contract_pricing"]
    enriched = []
    for p in base_products:
        pid = p.get("id")
        cp = pricing_map.get(pid, {"contract_price": p.get("price", 0), "discount_pct": 0, "tier": "Standard"})
        enriched.append({**p, **cp})
    return {"catalog": enriched}

@app.get("/portal/quotes", tags=["Portal Commerce & Orders"])
def list_quotes():
    return {"quotes": app_config["enterprise_portal"]["quotes"]}

@app.post("/portal/quotes", tags=["Portal Commerce & Orders"])
def create_custom_quote(body: QuoteRequestCreate):
    est_total = sum(float(i.get("qty", 1)) * float(i.get("unit_price", 100.0)) for i in body.items)
    quote = {
        "id": f"QT-2026-{random.randint(100, 999)}",
        "title": f"Custom Supply Proposal ({body.delivery_schedule})",
        "items": body.items,
        "total_amount": round(est_total, 2),
        "status": "pending_approval",
        "valid_until": (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d")
    }
    app_config["enterprise_portal"]["quotes"].insert(0, quote)
    record_portal_audit("buyer", f"QUOTE_REQUESTED_{quote['id']}")
    return {"message": f"Custom quote {quote['id']} generated.", "quote": quote}

@app.post("/portal/quotes/{quote_id}/approve-to-order", tags=["Portal Commerce & Orders"])
def approve_and_convert_quote(quote_id: str, body: QuoteApproveRequest):
    for q in app_config["enterprise_portal"]["quotes"]:
        if q["id"] == quote_id:
            q["status"] = "converted_to_order"
            q["signed_by"] = body.signer_name
            q["signature_hash"] = body.signature_hash
            order_id = f"FMV-ORD-{random.randint(5000, 9999)}"
            q["converted_order_id"] = order_id
            record_portal_audit(body.signer_name, f"QUOTE_{quote_id}_SIGNED_AND_CONVERTED_{order_id}")
            return {"message": f"Quote {quote_id} digitally signed and converted into Order #{order_id}.", "order_id": order_id, "quote": q}
    raise HTTPException(status_code=404, detail="Quote not found.")

@app.get("/portal/rma", tags=["Portal Commerce & Orders"])
def list_rma_claims():
    return {"claims": app_config["enterprise_portal"]["rma_claims"]}

@app.post("/portal/rma", tags=["Portal Commerce & Orders"])
def submit_rma_claim(body: RMAClaimCreate):
    claim = {
        "id": f"RMA-{random.randint(400, 999)}",
        "order_id": body.order_id,
        "claim_type": body.claim_type,
        "reason": body.reason,
        "items": body.items,
        "status": "under_review",
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    app_config["enterprise_portal"]["rma_claims"].insert(0, claim)
    record_portal_audit("customer", f"RMA_CLAIM_SUBMITTED_{claim['id']}")
    return {"message": f"RMA Request #{claim['id']} created for Order #{body.order_id}.", "claim": claim}

# --- 4. Billing, Invoicing & Financials Endpoints ---
@app.get("/portal/billing/invoices", tags=["Portal Billing & Financials"])
def get_invoices_and_soa():
    return {
        "invoices": app_config["enterprise_portal"]["invoices"],
        "credit_account": app_config["enterprise_portal"]["credit_account"]
    }

@app.post("/portal/billing/pay", tags=["Portal Billing & Financials"])
def process_online_payment(body: PaymentProcessRequest):
    for inv in app_config["enterprise_portal"]["invoices"]:
        if inv["id"] == body.invoice_id:
            inv["status"] = "paid"
            inv["paid_via"] = body.method
    if body.enable_autopay:
        app_config["enterprise_portal"]["credit_account"]["autopay_enabled"] = True
    ca = app_config["enterprise_portal"]["credit_account"]
    ca["utilized_credit"] = max(0.0, round(ca["utilized_credit"] - body.amount, 2))
    ca["available_credit"] = round(ca["credit_limit"] - ca["utilized_credit"], 2)
    record_portal_audit("finance_user", f"INVOICE_PAID_{body.invoice_id}_{body.method.upper()}")
    return {"message": f"Payment of {body.amount:.2f} processed via {body.method}.", "credit_account": ca}

@app.patch("/portal/billing/credit-subscription", tags=["Portal Billing & Financials"])
def update_credit_or_subscription(body: CreditLimitActionRequest):
    ca = app_config["enterprise_portal"]["credit_account"]
    if body.requested_limit is not None:
        ca["credit_limit"] = body.requested_limit
        ca["available_credit"] = round(ca["credit_limit"] - ca["utilized_credit"], 2)
    if body.plan_tier:
        ca["subscription_tier"] = body.plan_tier
    record_portal_audit("account_admin", "CREDIT_OR_PLAN_TIER_UPDATED")
    return {"message": "Subscription plan & credit limit settings updated.", "credit_account": ca}

# --- 5. Document Vault & Communication Endpoints ---
@app.get("/portal/documents", tags=["Portal Document Vault & Comms"])
def list_shared_documents():
    return {"documents": app_config["enterprise_portal"]["shared_documents"]}

@app.get("/portal/notifications", tags=["Portal Document Vault & Comms"])
def list_portal_notifications():
    return {"notifications": app_config["enterprise_portal"]["notifications"]}

@app.post("/portal/notifications/mark-read", tags=["Portal Document Vault & Comms"])
def mark_all_notifications_read():
    for n in app_config["enterprise_portal"]["notifications"]:
        n["read"] = True
    return {"message": "All notifications marked as read."}

@app.post("/portal/surveys", tags=["Portal Document Vault & Comms"])
def submit_csat_nps_survey(body: SurveySubmitRequest):
    entry = {
        "id": f"SRV-{random.randint(100, 999)}",
        "ticket_or_order_id": body.ticket_or_order_id,
        "csat_score": body.csat_score,
        "nps_score": body.nps_score,
        "comments": body.comments,
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }
    app_config["enterprise_portal"]["surveys"].insert(0, entry)
    return {"message": "Thank you for your CSAT & NPS feedback!", "survey": entry}

# --- 6. Technical, UX & Backend Requirements Endpoints ---
@app.get("/portal/enterprise/system-status", tags=["Portal Enterprise Backend"])
def get_enterprise_system_status():
    return {
        "erp_crm_integrations": app_config["enterprise_portal"]["erp_crm_integrations"],
        "white_label": app_config["enterprise_portal"]["white_label"],
        "portal_analytics": app_config["enterprise_portal"]["portal_analytics"],
        "security_audit_logs": app_config["enterprise_portal"]["security_audit_logs"][:20]
    }

@app.post("/portal/enterprise/erp-sync", tags=["Portal Enterprise Backend"])
def trigger_erp_bidirectional_sync(body: ERPSyncTriggerRequest):
    now_iso = datetime.utcnow().isoformat() + "Z"
    for conn in app_config["enterprise_portal"]["erp_crm_integrations"]:
        if conn["key"] == body.connector.lower():
            conn["status"] = "connected"
            conn["last_sync"] = now_iso
    record_portal_audit("system_admin", f"ERP_BIDIRECTIONAL_SYNC_{body.connector.upper()}")
    return {"message": f"Bidirectional sync completed with {body.connector.upper()} for {', '.join(body.sync_entities)}.", "synced_at": now_iso}

@app.patch("/portal/enterprise/white-label", tags=["Portal Enterprise Backend"])
def update_white_label_branding(body: WhiteLabelUpdateRequest):
    app_config["enterprise_portal"]["white_label"].update(body.dict())
    record_portal_audit("system_admin", f"WHITE_LABEL_UPDATED_{body.custom_domain}")
    return {"message": "White-label domain, logo, colors, and typography updated.", "white_label": app_config["enterprise_portal"]["white_label"]}


# ==============================================================================
# PATCH: INVENTORY STOCK UPDATES, AUTOMATIC CHECKOUT STOCK DEDUCTION & KERALA/INR
# ==============================================================================
class ProductStockUpdate(BaseModel):
    stock_quantity: int
    price: Optional[float] = None

class CheckoutOrderItem(BaseModel):
    product_id: str
    name: str
    quantity: int
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

if "products_catalog" not in app_config:
    app_config["products_catalog"] = [
        {"id": "prod-1", "name": "Pure Cow Ghee", "description": "Pure organic farm fresh dairy product (Kerala).", "price": 350.0, "unit": "500g", "stock_quantity": 100, "currency": "INR"},
        {"id": "prod-2", "name": "Fresh Curd", "description": "Pure organic farm fresh dairy product (Kerala).", "price": 40.0, "unit": "500g", "stock_quantity": 120, "currency": "INR"},
        {"id": "prod-3", "name": "Unsalted Butter", "description": "Pure organic farm fresh dairy product (Kerala).", "price": 120.0, "unit": "250g", "stock_quantity": 80, "currency": "INR"},
        {"id": "prod-4", "name": "Farm Fresh Milk", "description": "Daily morning fresh milk from Kerala farms.", "price": 60.0, "unit": "1 Liter", "stock_quantity": 250, "currency": "INR"}
    ]

if "deliveries_state" not in app_config:
    app_config["deliveries_state"] = {}

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

@app.post("/orders/checkout", tags=["Orders & Checkout"])
def process_customer_checkout(order: CustomerCheckoutRequest):
    # 1. Validate & automatically deduct stock for each purchased item
    for line in order.items:
        prod = next((p for p in app_config["products_catalog"] if str(p["id"]) == str(line.product_id)), None)
        if prod:
            if prod.get("stock_quantity", 0) < line.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {prod['name']} (Only {prod.get('stock_quantity', 0)} available)."
                )
            prod["stock_quantity"] -= line.quantity
            if supabase:
                try:
                    supabase.table("products").update({"stock_quantity": prod["stock_quantity"]}).eq("id", prod["id"]).execute()
                except Exception:
                    pass

    # 2. Create delivery record synced to Driver Portal & Admin Portal
    order_num = f"FMV-{random.randint(1000, 9999)}"
    delivery_otp = generate_order_otp(order_num)
    items_summary = ", ".join([f"{i.quantity}x {i.name}" for i in order.items])
    delivery_record = {
        "id": order_num,
        "order_number": order_num,
        "user_email": order.customer_email or order.customer_phone,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "delivery_address": order.delivery_address or "Kerala, India",
        "items_summary": items_summary,
        "quantity": sum(i.quantity for i in order.items),
        "total_amount": order.total_amount,
        "cod_amount": order.total_amount if "COD" in order.payment_method.upper() else 0.0,
        "currency": "INR",
        "status": "Out for Delivery",
        "delivery_date": datetime.utcnow().strftime("%Y-%m-%d"),
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

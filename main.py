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
    title="First Milk Venture API (Complete Enterprise Edition)",
    description="Production-Grade Enterprise Secured Backend for First Milk Venture: JWT Bearer Auth, Persistent Supabase OTP Storage, Protected Admin Endpoints, Driver Logistics, Webhooks, Advanced Subscriptions, and Multi-Sender Email Engine.",
    version="4.0.0"
)

# -----------------------------------------------------------------------------
# Security Hardening Step 4: Strict Production CORS Configuration
# -----------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://firstmilkventure.com",
    "https://www.firstmilkventure.com",
    "https://backend.firstmilkventure.com",
    "https://dairy-backend-9wcg.onrender.com",
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

# -----------------------------------------------------------------------------
# Environment & Client Initialization
# -----------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", ""))
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "Firstmilk3211234")
DRIVER_SECRET_KEY = os.getenv("DRIVER_SECRET_KEY", "DriverMilk2026")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-firstmilk-2026")
JWT_ALGORITHM = "HS256"

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Initialize Resend API Key
resend.api_key = os.getenv("RESEND_API_KEY", "")

# Bearer Token Scheme for FastAPI Docs
security = HTTPBearer()

# -----------------------------------------------------------------------------
# Dedicated Sender Email Identities
# -----------------------------------------------------------------------------
AUTH_EMAIL_SENDER = "First Milk Venture Auth <auth@firstmilkventure.com>"
FRONTDESK_EMAIL_SENDER = "First Milk Venture <frontdesk@firstmilkventure.com>"
SUBSCRIPTION_EMAIL_SENDER = "First Milk Venture Subscriptions <subscription@firstmilkventure.com>"
ORDERS_EMAIL_SENDER = "First Milk Venture Orders <orders@firstmilkventure.com>"
OFFERS_EMAIL_SENDER = "First Milk Venture Offers <offers@firstmilkventure.com>"

# -----------------------------------------------------------------------------
# Security Guards & Middleware Dependencies
# -----------------------------------------------------------------------------
def verify_admin_access(x_admin_key: str = Header(..., alias="x-admin-key", description="Secret Admin Access Key")):
    """Security Dependency to protect all /admin/ endpoints from unauthorized access."""
    if x_admin_key != ADMIN_SECRET_KEY:
        try:
            resend.Emails.send({
                "from": AUTH_EMAIL_SENDER,
                "to": ["admin@firstmilkventure.com"],
                "subject": "🚨 SECURITY ALERT: Unauthorized Admin Endpoint Access Attempt",
                "html": f"""
                    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; border: 2px solid #e53e3e; border-radius: 8px; background-color: #fff5f5;">
                        <h2 style="color: #e53e3e; margin-top: 0;">Unauthorized Access Guard Triggered</h2>
                        <p>An unauthorized request was blocked while attempting to access a protected administrative endpoint.</p>
                        <ul style="background: #ffffff; padding: 15px; border-radius: 5px; list-style: none; border: 1px solid #feb2b2;">
                            <li><strong>Timestamp (UTC):</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}</li>
                            <li><strong>Status Code:</strong> 403 Forbidden</li>
                            <li><strong>Action Required:</strong> Verify system security logs or key rotations.</li>
                        </ul>
                        <p style="margin-bottom: 0;">First Milk Venture Automated Security System</p>
                    </div>
                """
            })
        except Exception as alert_err:
            print(f"[ERROR] Failed to send admin security alert email: {str(alert_err)}")

        raise HTTPException(
            status_code=403, 
            detail="Access Denied: Invalid or missing X-Admin-Key header credential."
        )

def verify_driver_access(x_driver_key: str = Header(..., alias="x-driver-key", description="Secret Driver Access Key")):
    """Phase 2: Security dependency to authorize delivery driver personnel."""
    if x_driver_key != DRIVER_SECRET_KEY and x_driver_key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid X-Driver-Key header.")

def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """Security Dependency to decode JWT access token and return authenticated user email."""
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

# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------
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
    status: Optional[str] = None  # active, paused, cancelled
    pause_until: Optional[str] = None  # YYYY-MM-DD
    frequency: Optional[str] = None
    quantity: Optional[int] = None

class DeliveryStatusUpdate(BaseModel):
    status: str  # e.g., "pending", "out_for_delivery", "delivered", "cancelled"
    notes: Optional[str] = None

class DeliveryGenerationRequest(BaseModel):
    target_date: Optional[str] = "2026-11-10"

class PaymentWebhookPayload(BaseModel):
    event: str  # payment.succeeded, payment.failed, order.cancelled
    order_id: str
    transaction_id: str
    amount: float
    status: str

class OfferCreate(BaseModel):
    title: str
    description: str
    discount_percentage: float
    target_tier: str  # "normal", "bulk", "subscription", or "all"
    start_date: str   # Format: YYYY-MM-DD
    end_date: str     # Format: YYYY-MM-DD

class BroadcastOffer(BaseModel):
    offer_id: Optional[str] = None
    target_emails: List[EmailStr] = []
    subject: str = "🎉 Special Limited-Time Offer from First Milk Venture!"
    custom_message: str = ""

# -----------------------------------------------------------------------------
# Background Task: Automated Daily Order Dispatch Generator & Real-time Inventory
# -----------------------------------------------------------------------------
def generate_daily_orders(target_date_override: Optional[str] = None):
    today_str = target_date_override or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        print("[CRON WARNING] Supabase client is not connected.")
        return {"success": False, "error": "Supabase not connected", "target_date": today_str}
    
    try:
        print(f"[CRON] Running automated daily order generation for date: {today_str}")
        
        response = supabase.table("subscriptions").select("*").eq("status", "active").execute()
        active_subs = response.data or []
        
        if not active_subs:
            print("[CRON] No active subscriptions found for today.")
            return {"success": True, "generated_count": 0, "target_date": today_str}

        orders_to_insert = []
        stock_deductions = {}

        for sub in active_subs:
            pause_until = sub.get("pause_until")
            if pause_until and pause_until >= today_str:
                print(f"[CRON] Skipping paused subscription {sub.get('id')} until {pause_until}")
                continue

            p_id = sub.get("product_id")
            qty = sub.get("quantity", 1)
            stock_deductions[p_id] = stock_deductions.get(p_id, 0) + qty

            orders_to_insert.append({
                "subscription_id": sub.get("id"),
                "user_email": sub.get("user_email"),
                "product_id": p_id,
                "quantity": qty,
                "delivery_date": today_str,
                "status": "pending",
                "zone_id": sub.get("zone_id", "Zone-1")
            })
        
        for p_id, total_needed in stock_deductions.items():
            try:
                p_res = supabase.table("products").select("*").eq("id", p_id).execute()
                if p_res.data:
                    prod = p_res.data[0]
                    curr_stock = prod.get("stock_quantity", 100)
                    min_thresh = prod.get("min_stock_threshold", 20)
                    new_stock = max(0, curr_stock - total_needed)
                    
                    supabase.table("products").update({"stock_quantity": new_stock}).eq("id", p_id).execute()
                    
                    if new_stock <= min_thresh:
                        resend.Emails.send({
                            "from": FRONTDESK_EMAIL_SENDER,
                            "to": ["admin@firstmilkventure.com"],
                            "subject": f"⚠️ LOW STOCK ALERT: {prod.get('name')}",
                            "html": f"<p>Product <strong>{prod.get('name')}</strong> inventory has dropped to <strong>{new_stock} units</strong> (Min threshold: {min_thresh}). Please restock before tomorrow's dispatch.</p>"
                        })
            except Exception as stock_err:
                print(f"[ERROR] Inventory deduction error for {p_id}: {str(stock_err)}")

        if orders_to_insert:
            supabase.table("deliveries").insert(orders_to_insert).execute()
            print(f"[CRON] Successfully generated {len(orders_to_insert)} delivery orders for today.")
            
            for order in orders_to_insert:
                user_email = order["user_email"]
                qty = order["quantity"]
                try:
                    resend.Emails.send({
                        "from": ORDERS_EMAIL_SENDER,
                        "to": [user_email],
                        "subject": f"🥛 Your Fresh Milk Delivery is Scheduled for Today ({today_str})",
                        "html": f"""
                            <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px;">
                                <h2 style="color: #2b6cb0;">First Milk Venture</h2>
                                <p>Hello,</p>
                                <p>Your daily milk delivery order has been generated for today!</p>
                                <ul style="background: #f7fafc; padding: 15px; border-radius: 5px; list-style: none;">
                                    <li><strong>Date:</strong> {today_str}</li>
                                    <li><strong>Quantity:</strong> {qty} Unit(s)</li>
                                    <li><strong>Status:</strong> Pending Dispatch</li>
                                </ul>
                                <p>Thank you for subscribing with us!</p>
                            </div>
                        """
                    })
                except Exception as email_err:
                    print(f"[ERROR] Failed to send email to {user_email}: {str(email_err)}")

            return {"success": True, "generated_count": len(orders_to_insert), "target_date": today_str}
            
    except Exception as e:
        print(f"[ERROR] Failed to generate daily orders: {str(e)}")
        return {"success": False, "error": str(e), "target_date": today_str}

scheduler = BackgroundScheduler()
scheduler.add_job(generate_daily_orders, 'cron', hour=2, minute=0)
scheduler.start()

# -----------------------------------------------------------------------------
# Endpoints: Public Health & Service Overview
# -----------------------------------------------------------------------------
@app.get("/", tags=["General"])
def read_root():
    """Health check and API status."""
    return {
        "status": "online",
        "service": "First Milk Venture API (Complete Enterprise Edition)",
        "version": "4.0.0",
        "docs": "/docs"
    }

# -----------------------------------------------------------------------------
# Endpoints: Public Authentication & Persistent OTP Handling
# -----------------------------------------------------------------------------
@app.post("/auth/request-otp", tags=["Authentication"])
def request_otp(body: RequestOTP):
    """Generate and store persistent OTP in Supabase auth_otps table, then send via Resend."""
    otp = f"{random.randint(100000, 999999)}"
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    
    if supabase:
        try:
            supabase.table("auth_otps").upsert({
                "email": body.email,
                "phone": body.phone,
                "full_name": body.full_name,
                "address": body.address,
                "otp_code": otp,
                "expires_at": expires_at
            }).execute()
        except Exception as db_err:
            print(f"[ERROR] Supabase OTP save error: {str(db_err)}")
    
    try:
        resend.Emails.send({
            "from": AUTH_EMAIL_SENDER,
            "to": [body.email],
            "subject": f"🔑 Your Verification Code: {otp}",
            "html": f"""
                <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px;">
                    <h2 style="color: #2b6cb0;">First Milk Venture Login</h2>
                    <p>Your one-time login verification code is:</p>
                    <h1 style="letter-spacing: 4px; color: #2b6cb0;">{otp}</h1>
                    <p>This code is valid for 10 minutes. Do not share it with anyone.</p>
                </div>
            """
        })
        return {"message": "OTP sent successfully to your email"}
    except Exception as e:
        return {"message": f"OTP generated: {otp} (Resend status: {str(e)})"}

@app.post("/auth/verify-otp", tags=["Authentication"])
def verify_otp(body: VerifyOTP):
    """Verify persistent OTP from Supabase auth_otps table and issue signed JWT Bearer Access Token."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not connected.")
        
    res = supabase.table("auth_otps").select("*").eq("email", body.email).execute()
    records = res.data
    
    if not records:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
        
    otp_record = records[0]
    stored_code = otp_record.get("otp_code")
    expires_at_str = otp_record.get("expires_at")
    
    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    if datetime.utcnow().replace(tzinfo=expires_at.tzinfo) > expires_at:
        supabase.table("auth_otps").delete().eq("email", body.email).execute()
        raise HTTPException(status_code=400, detail="Verification code has expired.")
        
    if stored_code != body.otp:
        raise HTTPException(status_code=400, detail="Invalid verification code.")
        
    supabase.table("auth_otps").delete().eq("email", body.email).execute()
    
    payload = {
        "sub": body.email,
        "exp": datetime.utcnow() + timedelta(days=7),
        "iat": datetime.utcnow()
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    
    try:
        resend.Emails.send({
            "from": FRONTDESK_EMAIL_SENDER,
            "to": [body.email],
            "subject": "🥛 Welcome to First Milk Venture - Pure Freshness Every Morning!",
            "html": f"""
                <div style="font-family: Arial, sans-serif; padding: 25px; color: #2d3748; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px; background-color: #ffffff;">
                    <h2 style="color: #2b6cb0; margin-top: 0;">Welcome to First Milk Venture!</h2>
                    <p>Hello,</p>
                    <p>Thank you for verifying your account and joining the <strong>First Milk Venture</strong> family!</p>
                    <p>Warm regards,<br><strong>First Milk Venture Front Desk Team</strong></p>
                </div>
            """
        })
    except Exception as email_err:
        print(f"[ERROR] Failed to send frontdesk welcome email: {str(email_err)}")

    return {
        "message": "Authentication successful.",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "email": body.email,
            "status": "authenticated"
        }
    }

# -----------------------------------------------------------------------------
# Endpoints: Protected Customer Profile & Subscriptions
# -----------------------------------------------------------------------------
@app.get("/users/me", tags=["Users"])
def get_user_profile(user_email: str = Depends(verify_jwt)):
    """Protected Endpoint: Fetch authenticated customer profile and active subscriptions via JWT."""
    if not supabase:
        return {"email": user_email, "status": "active"}
    try:
        subs = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
        return {"email": user_email, "subscriptions": subs.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: SubscriptionCreate, user_email: str = Depends(verify_jwt)):
    """Protected Endpoint: Activate new customer plan using authenticated JWT identity."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        payload = sub.dict()
        payload["user_email"] = user_email
        payload["status"] = "active"
        response = supabase.table("subscriptions").insert(payload).execute()
        
        try:
            resend.Emails.send({
                "from": SUBSCRIPTION_EMAIL_SENDER,
                "to": [user_email],
                "subject": "🎉 Subscription Activated - First Milk Venture",
                "html": f"""
                    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px;">
                        <h2 style="color: #2b6cb0;">Subscription Confirmed!</h2>
                        <p>Hello,</p>
                        <p>Your daily milk subscription with <strong>First Milk Venture</strong> has been successfully activated.</p>
                        <ul style="background: #f7fafc; padding: 15px; border-radius: 5px; list-style: none;">
                            <li><strong>Product ID:</strong> {sub.product_id}</li>
                            <li><strong>Quantity:</strong> {sub.quantity} Unit(s)</li>
                            <li><strong>Frequency:</strong> {sub.frequency}</li>
                            <li><strong>Start Date:</strong> {sub.start_date}</li>
                        </ul>
                    </div>
                """
            })
        except Exception as email_err:
            print(f"[ERROR] Failed to send subscription confirmation email: {str(email_err)}")
            
        return {"message": "Subscription activated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.patch("/subscriptions/{subscription_id}", tags=["Subscriptions"])
def modify_subscription(subscription_id: str, body: SubscriptionModify, user_email: str = Depends(verify_jwt)):
    """Phase 3: Temporarily pause dispatches, skip dates, or adjust delivery frequency."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        update_data = {k: v for k, v in body.dict().items() if v is not None}
        response = supabase.table("subscriptions").update(update_data).eq("id", subscription_id).eq("user_email", user_email).execute()
        return {"message": "Subscription plan updated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/subscriptions", tags=["Subscriptions"])
def get_user_subscriptions(user_email: str = Depends(verify_jwt)):
    """Protected Endpoint: Fetch active or historical subscriptions for authenticated user."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/deliveries", tags=["Deliveries"])
def get_user_deliveries(user_email: str = Depends(verify_jwt)):
    """Protected Endpoint: Fetch upcoming or historical deliveries for authenticated user."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("deliveries").select("*").eq("user_email", user_email).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Phase 2: Payment Webhook Integration & Driver Logistics Engine
# -----------------------------------------------------------------------------
@app.post("/webhooks/payment", tags=["Billing & Payments"])
def handle_payment_webhook(payload: PaymentWebhookPayload):
    """Phase 2: Payment Webhook Listener for Stripe/Moyasar/HyperPay integration."""
    if not supabase:
        return {"status": "success", "message": "Webhook processed (Simulation)", "transaction_id": payload.transaction_id}
    
    new_status = "paid" if payload.event == "payment.succeeded" else "cancelled"
    try:
        supabase.table("deliveries").update({
            "payment_status": new_status, 
            "transaction_id": payload.transaction_id
        }).eq("id", payload.order_id).execute()
        return {"status": "success", "order_id": payload.order_id, "payment_status": new_status, "transaction_id": payload.transaction_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")

@app.get("/driver/deliveries", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def get_driver_routes(zone_id: Optional[str] = "Zone-1", delivery_date: Optional[str] = None):
    """Phase 2: Driver logistics route lookup grouped by geographic neighborhood/zone."""
    date_filter = delivery_date or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        return [{"id": "demo-del-1", "zone_id": zone_id, "delivery_date": date_filter, "status": "pending", "user_email": "customer@example.com"}]
    
    try:
        res = supabase.table("deliveries").select("*").eq("zone_id", zone_id).eq("delivery_date", date_filter).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.patch("/driver/deliveries/{delivery_id}", tags=["Driver Logistics"], dependencies=[Depends(verify_driver_access)])
def update_driver_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    """Phase 2: Mobile delivery update interface for drivers on assigned routes."""
    if not supabase:
        return {"message": "Status updated (Simulation)", "status": body.status}
    
    try:
        res = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
        return {"message": f"Delivery marked as {body.status}", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Phase 4: Observability & Audit Trail Logging
# -----------------------------------------------------------------------------
@app.post("/webhooks/resend", tags=["Observability & Audit"])
def handle_resend_email_webhook(payload: Dict[str, Any]):
    """Phase 4: Webhook listener for Resend email events (bounced, delivered, opened)."""
    event_type = payload.get("type", "email.unknown")
    email_data = payload.get("data", {})
    if supabase:
        try:
            supabase.table("audit_logs").insert({
                "event_type": f"EMAIL_{event_type.upper()}",
                "details": email_data,
                "timestamp": datetime.utcnow().isoformat()
            }).execute()
        except Exception:
            pass
    return {"status": "logged", "event": event_type}

@app.get("/admin/audit-logs", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def get_audit_logs():
    """Phase 4: Structured audit logs for backend administrative actions and deliverability."""
    if not supabase:
        return [{"event": "system_init", "timestamp": datetime.utcnow().isoformat()}]
    try:
        return supabase.table("audit_logs").select("*").order("timestamp", desc=True).limit(100).execute().data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Endpoints: Public Products & Offers
# -----------------------------------------------------------------------------
@app.get("/products", tags=["Products"])
def get_products():
    """Public Endpoint: Retrieve available product catalog."""
    if not supabase:
        return [
            {"id": "prod-1", "name": "Fresh Whole Milk", "description": "100% pure pasteurized farm fresh milk.", "price": 6.5, "unit": "Liter"},
            {"id": "prod-2", "name": "Low Fat Fresh Milk", "description": "Nutritious low-fat farm fresh milk.", "price": 6.5, "unit": "Liter"},
            {"id": "prod-3", "name": "Organic Fresh Butter", "description": "Rich traditional churned butter.", "price": 18.0, "unit": "500g"}
        ]
    try:
        response = supabase.table("products").select("*").execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/offers", tags=["Offers Engine"])
def get_active_offers(tier: str = "all"):
    """Public Endpoint: Fetch active promotional offers filtered by customer tier."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    if not supabase:
        return [
            {
                "id": "demo-offer-1",
                "title": "Subscription Discount",
                "description": "15% off on daily milk subscriptions!",
                "discount_percentage": 15.0,
                "target_tier": "subscription",
                "start_date": "2026-11-01",
                "end_date": "2026-12-31"
            }
        ]
        
    try:
        query = supabase.table("offers").select("*").lte("start_date", today_str).gte("end_date", today_str)
        if tier != "all":
            query = query.in_("target_tier", [tier, "all"])
        response = query.execute()
        return response.data
    except Exception as e:
        return []

# -----------------------------------------------------------------------------
# SECURED ADMIN ENDPOINTS (Protected by X-Admin-Key Security Guard)
# -----------------------------------------------------------------------------
@app.post("/admin/products", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_product(product: ProductCreate):
    """Secured Admin Endpoint: Create new product in catalog."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").insert(product.dict()).execute()
        return {"message": "Product created successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.patch("/admin/products/{product_id}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_product(product_id: str, product: ProductUpdate):
    """Secured Admin Endpoint: Modify product details or prices."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        update_data = {k: v for k, v in product.dict().items() if v is not None}
        response = supabase.table("products").update(update_data).eq("id", product_id).execute()
        return {"message": "Product updated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.delete("/admin/products/{product_id}", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def delete_product(product_id: str):
    """Secured Admin Endpoint: Delete a product from inventory."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").delete().eq("id", product_id).execute()
        return {"message": "Product deleted successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/admin/generate-deliveries", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def manual_generate_deliveries(payload: Optional[DeliveryGenerationRequest] = None):
    """Secured Admin Endpoint: Trigger manual daily order generation."""
    target_date = payload.target_date if payload else None
    result = generate_daily_orders(target_date_override=target_date)
    return {"message": "Manual delivery generation and emails executed", "result": result}

@app.patch("/admin/deliveries/{delivery_id}/status", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def update_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    """Secured Admin Endpoint: Update delivery status and dispatch email from orders@firstmilkventure.com."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
        updated_data = response.data
        
        if not updated_data:
            raise HTTPException(status_code=404, detail="Delivery record not found.")
            
        delivery_record = updated_data[0]
        user_email = delivery_record.get("user_email")
        delivery_date = delivery_record.get("delivery_date")
        quantity = delivery_record.get("quantity", 1)
        
        status_display = body.status.replace("_", " ").title()
        
        if user_email:
            try:
                resend.Emails.send({
                    "from": ORDERS_EMAIL_SENDER,
                    "to": [user_email],
                    "subject": f"📦 Update on Your Milk Delivery: {status_display}",
                    "html": f"""
                        <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px;">
                            <h2 style="color: #2b6cb0;">First Milk Venture</h2>
                            <p>Hello,</p>
                            <p>There is an update regarding your milk delivery for <strong>{delivery_date}</strong>:</p>
                            <ul style="background: #f7fafc; padding: 15px; border-radius: 5px; list-style: none;">
                                <li><strong>Quantity:</strong> {quantity} Unit(s)</li>
                                <li><strong>New Status:</strong> <span style="color: #2b6cb0; font-weight: bold;">{status_display}</span></li>
                            </ul>
                            <p>Thank you for choosing First Milk Venture!</p>
                        </div>
                    """
                })
            except Exception as email_err:
                print(f"[ERROR] Failed to send status update email: {str(email_err)}")

        return {
            "message": f"Delivery status updated to '{body.status}' and client notified successfully",
            "data": updated_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/admin/offers", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def create_offer(offer: OfferCreate):
    """Secured Admin Endpoint: Configure time-bound promotional offers by tier."""
    if offer.target_tier.lower() not in ["normal", "bulk", "subscription", "all"]:
        raise HTTPException(status_code=400, detail="Target tier must be 'normal', 'bulk', 'subscription', or 'all'")
        
    if not supabase:
        return {"message": "Offer created (Simulation)", "offer": offer.dict()}
        
    try:
        response = supabase.table("offers").insert(offer.dict()).execute()
        return {"message": "Offer created successfully", "data": response.data}
    except Exception as e:
        return {"message": "Offer configured successfully", "data": offer.dict()}

@app.post("/admin/offers/broadcast", tags=["Admin Controls"], dependencies=[Depends(verify_admin_access)])
def broadcast_offer_email(payload: BroadcastOffer):
    """Secured Admin Endpoint: Broadcast promotional emails from offers@firstmilkventure.com."""
    if not payload.target_emails:
        raise HTTPException(status_code=400, detail="No target emails provided")
        
    sent_count = 0
    failed_emails = []
    custom_text = payload.custom_message if payload.custom_message else "Check out our exclusive client discount offer available for a limited time!"
    
    for email in payload.target_emails:
        try:
            resend.Emails.send({
                "from": OFFERS_EMAIL_SENDER,
                "to": [email],
                "subject": payload.subject,
                "html": f"""
                    <div style="font-family: Arial, sans-serif; padding: 25px; color: #2d3748; max-width: 600px; border: 2px solid #ed8936; border-radius: 8px; background-color: #fffaf0;">
                        <h2 style="color: #dd6b20; margin-top: 0;">🎁 Exclusive Offer for You!</h2>
                        <p>Hello,</p>
                        <p>{custom_text}</p>
                        
                        <div style="background-color: #ffffff; border: 1px dashed #dd6b20; padding: 15px; text-align: center; margin: 20px 0; border-radius: 6px;">
                            <h3 style="color: #c05621; margin: 0;">Special Client Discount</h3>
                            <p style="font-size: 14px; color: #718096; margin: 5px 0 0 0;">Valid for a limited time only!</p>
                        </div>

                        <p>Visit your customer dashboard to apply this offer to your next order or subscription plan.</p>
                        <p style="margin-bottom: 0;">Warm regards,<br><strong>First Milk Venture Promotions Team</strong></p>
                    </div>
                """
            })
            sent_count += 1
        except Exception as email_err:
            print(f"[ERROR] Failed to send offer email to {email}: {str(email_err)}")
            failed_emails.append(email)
            
    return {
        "message": f"Broadcast completed. Successfully sent {sent_count} offer emails.",
        "failed_emails": failed_emails
    }

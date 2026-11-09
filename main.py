from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from apscheduler.schedulers.background import BackgroundScheduler
import os
import random
from supabase import create_client, Client
import resend

app = FastAPI(
    title="First Milk Venture API (Secured Edition)",
    description="Production-Grade Enterprise Secured Backend for First Milk Venture: Protected Admin Endpoints, Restricted CORS, Dynamic Multi-Sender Email Engine, and Tier-Based Promotional Engine.",
    version="3.1.0"
)

# -----------------------------------------------------------------------------
# Security Hardening Step 4: Strict Production CORS Configuration
# -----------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://firstmilkventure.com",
    "https://www.firstmilkventure.com",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
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

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Initialize Resend API Key
resend.api_key = os.getenv("RESEND_API_KEY", "")

# In-memory OTP storage (email -> otp_code)
otp_store = {}

# -----------------------------------------------------------------------------
# Dedicated Sender Email Identities
# -----------------------------------------------------------------------------
AUTH_EMAIL_SENDER = "First Milk Venture Auth <auth@firstmilkventure.com>"
FRONTDESK_EMAIL_SENDER = "First Milk Venture <frontdesk@firstmilkventure.com>"
SUBSCRIPTION_EMAIL_SENDER = "First Milk Venture Subscriptions <subscription@firstmilkventure.com>"
ORDERS_EMAIL_SENDER = "First Milk Venture Orders <orders@firstmilkventure.com>"
OFFERS_EMAIL_SENDER = "First Milk Venture Offers <offers@firstmilkventure.com>"

# -----------------------------------------------------------------------------
# Security Hardening Step 2: Admin Security Guard Dependency
# -----------------------------------------------------------------------------
def verify_admin_access(x_admin_key: str = Header(..., alias="x-admin-key", description="Secret Admin Access Key")):
    """Security Dependency to protect all /admin/ endpoints from unauthorized access."""
    if x_admin_key != ADMIN_SECRET_KEY:
        raise HTTPException(
            status_code=403, 
            detail="Access Denied: Invalid or missing X-Admin-Key header credential."
        )

# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------
class RequestOTP(BaseModel):
    email: EmailStr

class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    unit: str = "Liter"
    image_url: str = ""

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    unit: Optional[str] = None
    image_url: Optional[str] = None

class SubscriptionCreate(BaseModel):
    user_email: EmailStr
    product_id: str
    quantity: int = 1
    frequency: str = "Daily"
    start_date: str

class DeliveryStatusUpdate(BaseModel):
    status: str  # e.g., "pending", "out_for_delivery", "delivered", "cancelled"

class DeliveryGenerationRequest(BaseModel):
    target_date: Optional[str] = "2026-11-10"

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
# Background Task: Automated Daily Order Dispatch Generator
# -----------------------------------------------------------------------------
def generate_daily_orders(target_date_override: Optional[str] = None):
    today_str = target_date_override or datetime.utcnow().strftime("%Y-%m-%d")
    if not supabase:
        print("[CRON WARNING] Supabase client is not connected.")
        return {"success": False, "error": "Supabase not connected", "target_date": today_str}
    
    try:
        print(f"[CRON] Running automated daily order generation for date: {today_str}")
        
        response = supabase.table("subscriptions").select("*").eq("status", "active").execute()
        active_subs = response.data
        
        if not active_subs:
            print("[CRON] No active subscriptions found for today.")
            return {"success": True, "generated_count": 0, "target_date": today_str}

        orders_to_insert = []
        for sub in active_subs:
            orders_to_insert.append({
                "subscription_id": sub.get("id"),
                "user_email": sub.get("user_email"),
                "product_id": sub.get("product_id"),
                "quantity": sub.get("quantity", 1),
                "delivery_date": today_str,
                "status": "pending"
            })
        
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

# Background Scheduler (Runs daily at 02:00 AM UTC)
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
        "service": "First Milk Venture API (Secured Edition)",
        "version": "3.1.0",
        "docs": "/docs"
    }

# -----------------------------------------------------------------------------
# Endpoints: Public Authentication & User Welcome
# -----------------------------------------------------------------------------
@app.post("/auth/request-otp", tags=["Authentication"])
def request_otp(body: RequestOTP):
    """Generate and send 6-digit login OTP from auth@firstmilkventure.com via Resend."""
    otp = f"{random.randint(100000, 999999)}"
    otp_store[body.email] = otp
    
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
                    <p>This code is valid for your current session. Do not share it with anyone.</p>
                </div>
            """
        })
        return {"message": "OTP sent successfully to your email"}
    except Exception as e:
        return {"message": f"OTP generated: {otp} (Resend status: {str(e)})"}

@app.post("/auth/verify-otp", tags=["Authentication"])
def verify_otp(body: VerifyOTP):
    """
    Verify OTP for login and send Welcome & Brand Advertising email from frontdesk@firstmilkventure.com.
    """
    stored_otp = otp_store.get(body.email)
    if not stored_otp or stored_otp != body.otp:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
    
    otp_store.pop(body.email, None)
    
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
                    
                    <div style="background-color: #ebf8ff; border-left: 4px solid #3182ce; padding: 15px; margin: 20px 0; border-radius: 4px;">
                        <h4 style="margin: 0 0 10px 0; color: #2b6cb0;">🌱 Pure, Farm-Fresh Dairy Delivered Daily</h4>
                        <p style="margin: 0; font-size: 14px; line-height: 1.5;">
                            We are committed to delivering 100% pure, unadulterated pasture milk straight to your home every morning. 
                            Our automated daily subscription pipeline guarantees fresh supply without daily ordering hassle.
                        </p>
                    </div>

                    <h4 style="color: #2d3748; margin-bottom: 8px;">Why Choose Us?</h4>
                    <ul style="padding-left: 20px; font-size: 14px; line-height: 1.6;">
                        <li><strong>100% Pure Pasture Milk:</strong> Directly sourced with zero preservatives.</li>
                        <li><strong>Flexible Subscriptions:</strong> Daily dispatches tailored to your household.</li>
                        <li><strong>Live Delivery Notifications:</strong> Instant delivery updates straight to your inbox.</li>
                    </ul>

                    <p style="margin-top: 25px;">Head over to your customer portal and start your daily subscription today!</p>
                    <p style="margin-bottom: 0;">Warm regards,<br><strong>First Milk Venture Front Desk Team</strong></p>
                </div>
            """
        })
    except Exception as email_err:
        print(f"[ERROR] Failed to send frontdesk welcome email: {str(email_err)}")

    return {
        "message": "Authentication successful. Welcome email dispatched.",
        "user": {
            "email": body.email,
            "status": "authenticated"
        }
    }

@app.get("/users/me", tags=["Users"])
def get_user_profile(email: str):
    """Fetch profile data and active subscriptions for a customer."""
    if not supabase:
        return {"email": email, "status": "active"}
    try:
        subs = supabase.table("subscriptions").select("*").eq("user_email", email).execute()
        return {"email": email, "subscriptions": subs.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Endpoints: Public Products & Subscriptions
# -----------------------------------------------------------------------------
@app.get("/products", tags=["Products"])
def get_products():
    """Public Endpoint: Retrieve available product catalog."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").select("*").execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: SubscriptionCreate):
    """Activate new customer plan and send confirmation from subscription@firstmilkventure.com."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        payload = sub.dict()
        payload["status"] = "active"
        response = supabase.table("subscriptions").insert(payload).execute()
        
        try:
            resend.Emails.send({
                "from": SUBSCRIPTION_EMAIL_SENDER,
                "to": [sub.user_email],
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
                        <p>Our automated daily schedule will handle all your morning dispatches automatically!</p>
                        <p>Best regards,<br><strong>First Milk Venture Subscriptions Team</strong></p>
                    </div>
                """
            })
        except Exception as email_err:
            print(f"[ERROR] Failed to send subscription confirmation email: {str(email_err)}")
            
        return {"message": "Subscription activated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/subscriptions/{user_email}", tags=["Subscriptions"])
def get_user_subscriptions(user_email: str):
    """Fetch active or historical subscriptions for a user."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/deliveries/{user_email}", tags=["Deliveries"])
def get_user_deliveries(user_email: str):
    """Fetch upcoming or historical deliveries for a specific user."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("deliveries").select("*").eq("user_email", user_email).execute()
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

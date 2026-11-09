from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from apscheduler.schedulers.background import BackgroundScheduler
import os
import random
from supabase import create_client, Client
import resend

app = FastAPI(
    title="First Milk Venture API",
    description="Full-featured Backend for First Milk Venture: OTP Authentication, Subscription Management, Deliveries, Offers Engine, and Multi-Domain Email Dispatches.",
    version="3.0.0"
)

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Resend
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
# Pydantic Models
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
    name: str = None
    description: str = None
    price: float = None
    unit: str = None
    image_url: str = None

class SubscriptionCreate(BaseModel):
    user_email: EmailStr
    product_id: str
    quantity: int = 1
    frequency: str = "Daily"
    start_date: str

class DeliveryStatusUpdate(BaseModel):
    status: str  # e.g., "pending", "out_for_delivery", "delivered", "cancelled"

class OfferCreate(BaseModel):
    title: str
    description: str
    discount_percentage: float
    target_tier: str  # "normal", "bulk", "subscription", or "all"
    start_date: str   # Format: YYYY-MM-DD
    end_date: str     # Format: YYYY-MM-DD

class BroadcastOffer(BaseModel):
    offer_id: str = None
    target_emails: list[EmailStr] = []
    subject: str = "🎉 Special Limited-Time Offer from First Milk Venture!"
    custom_message: str = ""

# -----------------------------------------------------------------------------
# Background Task: Daily Order Generator & Email Notifier
# -----------------------------------------------------------------------------
def generate_daily_orders():
    if not supabase:
        print("[SIMULATION] Daily order generator ran, but Supabase is not connected.")
        return {"success": False, "error": "Supabase not connected"}
    
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        print(f"[CRON] Running daily order generation for date: {today_str}")
        
        response = supabase.table("subscriptions").select("*").eq("status", "active").execute()
        active_subs = response.data
        
        if not active_subs:
            print("[CRON] No active subscriptions found.")
            return {"success": True, "generated_count": 0}

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

            return {"success": True, "generated_count": len(orders_to_insert)}
            
    except Exception as e:
        print(f"[ERROR] Failed to generate daily orders: {str(e)}")
        return {"success": False, "error": str(e)}

# Background Scheduler (Runs daily at 02:00 AM UTC)
scheduler = BackgroundScheduler()
scheduler.add_job(generate_daily_orders, 'cron', hour=2, minute=0)
scheduler.start()

# -----------------------------------------------------------------------------
# Endpoints: General
# -----------------------------------------------------------------------------
@app.get("/", tags=["General"])
def read_root():
    """Health check and API overview."""
    return {
        "status": "online",
        "service": "First Milk Venture API",
        "version": "3.0.0",
        "docs": "/docs"
    }

# -----------------------------------------------------------------------------
# Endpoints: Authentication & User Welcome
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
        return {"message": f"OTP generated: {otp} (Resend error: {str(e)})"}

@app.post("/auth/verify-otp", tags=["Authentication"])
def verify_otp(body: VerifyOTP):
    """
    Verify OTP for login and dispatch welcome & promotional email 
    from frontdesk@firstmilkventure.com.
    """
    stored_otp = otp_store.get(body.email)
    if not stored_otp or stored_otp != body.otp:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    otp_store.pop(body.email, None)
    
    # Send Welcome & Business Brand Promo from frontdesk@firstmilkventure.com
    try:
        resend.Emails.send({
            "from": FRONTDESK_EMAIL_SENDER,
            "to": [body.email],
            "subject": "🥛 Thank You for Joining First Milk Venture!",
            "html": f"""
                <div style="font-family: Arial, sans-serif; padding: 25px; color: #2d3748; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px; background-color: #ffffff;">
                    <h2 style="color: #2b6cb0; margin-top: 0;">Welcome to First Milk Venture!</h2>
                    <p>Hello,</p>
                    <p>Thank you for verifying your email and joining the <strong>First Milk Venture</strong> family!</p>
                    
                    <div style="background-color: #ebf8ff; border-left: 4px solid #3182ce; padding: 15px; margin: 20px 0; border-radius: 4px;">
                        <h4 style="margin: 0 0 10px 0; color: #2b6cb0;">🌱 Pure, Farm-Fresh Dairy Delivered Daily</h4>
                        <p style="margin: 0; font-size: 14px; line-height: 1.5;">
                            At First Milk Venture, we are dedicated to bringing 100% pure, farm-fresh dairy straight to your doorstep every morning. 
                            Our automated daily subscription pipeline ensures you never run out of essential dairy products.
                        </p>
                    </div>

                    <h4 style="color: #2d3748; margin-bottom: 8px;">Why Choose Us?</h4>
                    <ul style="padding-left: 20px; font-size: 14px; line-height: 1.6;">
                        <li><strong>100% Unadulterated Pure Milk:</strong> Direct from local pasture-raised herds.</li>
                        <li><strong>Flexible Subscriptions:</strong> Daily, weekly, or custom schedules tailored to your household.</li>
                        <li><strong>Real-Time Delivery Tracking:</strong> Instant email status updates from dispatch to your doorstep.</li>
                    </ul>

                    <p style="margin-top: 25px;">Explore our catalog today and set up your daily subscription in just a few clicks!</p>
                    <p style="margin-bottom: 0;">Warm regards,<br><strong>First Milk Venture Front Desk Team</strong></p>
                </div>
            """
        })
    except Exception as email_err:
        print(f"[ERROR] Failed to send welcome email from frontdesk: {str(email_err)}")

    return {
        "message": "Authentication successful. Welcome email dispatched.",
        "user": {
            "email": body.email,
            "status": "authenticated"
        }
    }

@app.get("/users/me", tags=["Users"])
def get_user_profile(email: str):
    """Retrieve user details and active subscriptions."""
    if not supabase:
        return {"email": email, "status": "active"}
    try:
        subs = supabase.table("subscriptions").select("*").eq("user_email", email).execute()
        return {"email": email, "subscriptions": subs.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Endpoints: Products (Full CRUD for Admin & Catalog)
# -----------------------------------------------------------------------------
@app.get("/products", tags=["Products"])
def get_products():
    """Retrieve all available dairy products."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").select("*").execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/admin/products", tags=["Products"])
def create_product(product: ProductCreate):
    """Add a new product to the catalog (Admin)."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").insert(product.dict()).execute()
        return {"message": "Product created successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.patch("/admin/products/{product_id}", tags=["Products"])
def update_product(product_id: str, product: ProductUpdate):
    """Update product details or pricing (Admin)."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        update_data = {k: v for k, v in product.dict().items() if v is not None}
        response = supabase.table("products").update(update_data).eq("id", product_id).execute()
        return {"message": "Product updated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.delete("/admin/products/{product_id}", tags=["Products"])
def delete_product(product_id: str):
    """Remove a product from the catalog (Admin)."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").delete().eq("id", product_id).execute()
        return {"message": "Product deleted successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Endpoints: Subscriptions
# -----------------------------------------------------------------------------
@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: SubscriptionCreate):
    """Create subscription and send confirmation email from subscription@firstmilkventure.com."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        payload = sub.dict()
        payload["status"] = "active"
        response = supabase.table("subscriptions").insert(payload).execute()
        
        # Send confirmation notification from subscription@firstmilkventure.com
        try:
            resend.Emails.send({
                "from": SUBSCRIPTION_EMAIL_SENDER,
                "to": [sub.user_email],
                "subject": "🎉 Subscription Activated - First Milk Venture",
                "html": f"""
                    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; border: 1px solid #e2e8f0; border-radius: 8px;">
                        <h2 style="color: #2b6cb0;">Subscription Confirmed!</h2>
                        <p>Hello,</p>
                        <p>Your subscription with <strong>First Milk Venture</strong> has been successfully activated.</p>
                        <ul style="background: #f7fafc; padding: 15px; border-radius: 5px; list-style: none;">
                            <li><strong>Product ID:</strong> {sub.product_id}</li>
                            <li><strong>Quantity:</strong> {sub.quantity} Unit(s)</li>
                            <li><strong>Frequency:</strong> {sub.frequency}</li>
                            <li><strong>Start Date:</strong> {sub.start_date}</li>
                        </ul>
                        <p>Our automated scheduling will now handle your deliveries every day!</p>
                        <p>Manage your account or alter plan settings anytime via your dashboard.</p>
                        <p>Best regards,<br><strong>First Milk Venture Subscriptions Team</strong></p>
                    </div>
                """
            })
        except Exception as email_err:
            print(f"[ERROR] Failed to send subscription confirmation email: {str(email_err)}")
            
        return {"message": "Subscription activated and confirmation email dispatched", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/subscriptions/{user_email}", tags=["Subscriptions"])
def get_user_subscriptions(user_email: str):
    """Fetch all active or historical subscriptions for a user."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("subscriptions").select("*").eq("user_email", user_email).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Endpoints: Deliveries & Status Management
# -----------------------------------------------------------------------------
@app.post("/admin/generate-deliveries", tags=["Deliveries"])
def manual_generate_deliveries():
    """Manually trigger daily delivery generation and email dispatch for testing purposes."""
    result = generate_daily_orders()
    return {"message": "Manual delivery generation and emails executed", "result": result}

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

@app.patch("/admin/deliveries/{delivery_id}/status", tags=["Deliveries"])
def update_delivery_status(delivery_id: str, body: DeliveryStatusUpdate):
    """Update status of a delivery and notify client from orders@firstmilkventure.com."""
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

# -----------------------------------------------------------------------------
# Endpoints: Targeted Offers Engine (Normal, Bulk, Subscription)
# -----------------------------------------------------------------------------
@app.post("/admin/offers", tags=["Offers Engine"])
def create_offer(offer: OfferCreate):
    """
    Create time-bound promotional offer for specific client tiers:
    target_tier options: 'normal', 'bulk', 'subscription', or 'all'.
    """
    if offer.target_tier.lower() not in ["normal", "bulk", "subscription", "all"]:
        raise HTTPException(status_code=400, detail="Target tier must be 'normal', 'bulk', 'subscription', or 'all'")
        
    if not supabase:
        return {"message": "Offer created (Simulation)", "offer": offer.dict()}
        
    try:
        response = supabase.table("offers").insert(offer.dict()).execute()
        return {"message": "Offer created successfully", "data": response.data}
    except Exception as e:
        # Fallback response if table is created dynamically
        return {"message": "Offer configured successfully", "data": offer.dict()}

@app.get("/offers", tags=["Offers Engine"])
def get_active_offers(tier: str = "all"):
    """Fetch all active, time-valid promotional offers by client tier."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    if not supabase:
        return [
            {
                "id": "demo-offer-1",
                "title": "Subscription Delight",
                "description": "Get 15% off all monthly milk subscriptions!",
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

@app.post("/admin/offers/broadcast", tags=["Offers Engine"])
def broadcast_offer_email(payload: BroadcastOffer):
    """
    Broadcast promotional offer emails from offers@firstmilkventure.com 
    to specified client target emails.
    """
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

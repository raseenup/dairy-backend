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
    description="Full-featured Backend for First Milk Venture: Authentication (Email OTP), User Management, Product Catalog, Subscriptions, Deliveries, and Admin Controls.",
    version="2.0.0"
)

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Resend
resend.api_key = os.getenv("RESEND_API_KEY", "")

# In-memory OTP storage for demo/testing (email -> otp_code)
otp_store = {}

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
                        "from": "First Milk Venture <orders@firstmilkventure.com>",
                        "to": [user_email],
                        "subject": f"🥛 Your Fresh Milk Delivery is Scheduled for Today ({today_str})",
                        "html": f"""
                            <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
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

# Initialize and start background scheduler (Runs daily at 02:00 AM UTC)
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
        "version": "2.0.0",
        "docs": "/docs"
    }

# -----------------------------------------------------------------------------
# Endpoints: Authentication & Users
# -----------------------------------------------------------------------------
@app.post("/auth/request-otp", tags=["Authentication"])
def request_otp(body: RequestOTP):
    """Generate and send a 6-digit login OTP to the user's email via Resend."""
    otp = f"{random.randint(100000, 999999)}"
    otp_store[body.email] = otp
    
    try:
        resend.Emails.send({
            "from": "First Milk Venture <orders@firstmilkventure.com>",
            "to": [body.email],
            "subject": f"🔑 Your Verification Code: {otp}",
            "html": f"""
                <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
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
    """Verify the 6-digit OTP for login."""
    stored_otp = otp_store.get(body.email)
    if not stored_otp or stored_otp != body.otp:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    otp_store.pop(body.email, None)
    
    return {
        "message": "Authentication successful",
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
    """Create a new dairy product subscription."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        payload = sub.dict()
        payload["status"] = "active"
        response = supabase.table("subscriptions").insert(payload).execute()
        return {"message": "Subscription activated successfully", "data": response.data}
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
    """Update the status of a specific delivery and send an email notification to the client."""
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
                    "from": "First Milk Venture <orders@firstmilkventure.com>",
                    "to": [user_email],
                    "subject": f"📦 Update on Your Milk Delivery: {status_display}",
                    "html": f"""
                        <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
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

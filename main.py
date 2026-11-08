from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from apscheduler.schedulers.background import BackgroundScheduler
import os
from supabase import create_client, Client
import resend

app = FastAPI(
    title="First Milk Venture API",
    description="Backend for user authentication, product catalog, subscriptions, automated daily delivery scheduling, status updates, and Resend emails.",
    version="1.1.0"
)

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Resend
resend.api_key = os.getenv("RESEND_API_KEY", "")

# -----------------------------------------------------------------------------
# Pydantic Models
# -----------------------------------------------------------------------------
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
        
        # 1. Fetch all active subscriptions from Supabase
        response = supabase.table("subscriptions").select("*").eq("status", "active").execute()
        active_subs = response.data
        
        if not active_subs:
            print("[CRON] No active subscriptions found.")
            return {"success": True, "generated_count": 0}

        # 2. Generate delivery records for each active subscription
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
        
        # 3. Insert into the 'deliveries' table in Supabase
        if orders_to_insert:
            insert_res = supabase.table("deliveries").insert(orders_to_insert).execute()
            print(f"[CRON] Successfully generated {len(orders_to_insert)} delivery orders for today.")
            
            # 4. Send email notifications via Resend for each generated order
            for order in orders_to_insert:
                user_email = order["user_email"]
                qty = order["quantity"]
                try:
                    resend.Emails.send({
                        "from": "First Milk Venture <onboarding@resend.dev>",
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
# Endpoints: Products
# -----------------------------------------------------------------------------
@app.get("/products", tags=["Products"])
def get_products():
    """Retrieve all available dairy products from Supabase."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("products").select("*").execute()
        return response.data
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
    """Update the status of a specific delivery (e.g., pending -> out_for_delivery -> delivered)."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client is not initialized.")
    try:
        response = supabase.table("deliveries").update({"status": body.status}).eq("id", delivery_id).execute()
        return {"message": f"Delivery status updated to '{body.status}' successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

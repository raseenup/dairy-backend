import os
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from apscheduler.schedulers.background import BackgroundScheduler
from supabase import create_client, Client

app = FastAPI(
    title="First Milk Venture API",
    description="Backend for user authentication, product catalog, subscriptions, and automated daily delivery scheduling.",
    version="1.0.0"
)

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------------------------------------------------------------
# Pydantic Models
# -----------------------------------------------------------------------------
class SubscriptionCreate(BaseModel):
    user_email: EmailStr
    product_id: str
    quantity: int = 1
    frequency: str = "Daily"
    start_date: str

# -----------------------------------------------------------------------------
# Background Task: Daily Order Generator
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
# Endpoints: Deliveries & Admin Automation
# -----------------------------------------------------------------------------
@app.post("/admin/generate-deliveries", tags=["Deliveries"])
def manual_generate_deliveries():
    """Manually trigger daily delivery generation for testing purposes."""
    result = generate_daily_orders()
    return {"message": "Manual delivery generation executed", "result": result}

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
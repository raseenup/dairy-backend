import os
import random
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
import resend
from supabase import create_client, Client

app = FastAPI(
    title="First Milk Venture API",
    description="Backend service for First Milk Venture with Email OTP authentication, Product Catalog, and Subscriptions.",
    version="1.1.1"
)

# -----------------------------------------------------------------------------
# Environment & Client Setup
# -----------------------------------------------------------------------------
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    # Auto-sanitize URL and API Key (removes trailing slashes, quotes, spaces)
    clean_url = SUPABASE_URL.strip().strip('"').strip("'").rstrip('/')
    clean_key = SUPABASE_KEY.strip().strip('"').strip("'")
    try:
        supabase = create_client(clean_url, clean_key)
    except Exception as e:
        print(f"[ERROR] Failed to initialize Supabase client: {e}")

# In-memory OTP storage for quick validation
otp_store = {}

# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------
class RequestOTP(BaseModel):
    email: EmailStr

class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str

class ProductResponse(BaseModel):
    id: str
    name: str
    category: str
    unit: str
    price: float
    is_available: bool

class CreateSubscription(BaseModel):
    user_email: EmailStr
    product_id: str
    quantity: int = 1
    frequency: str  # 'daily', 'alternate_days', 'weekly'
    start_date: str # Format: 'YYYY-MM-DD'

class SubscriptionResponse(BaseModel):
    id: str
    user_email: str
    product_id: str
    quantity: int
    frequency: str
    status: str
    start_date: str

# -----------------------------------------------------------------------------
# General Endpoints
# -----------------------------------------------------------------------------
@app.get("/", tags=["General"])
def read_root():
    return {
        "status": "online",
        "company": "First Milk Venture",
        "message": "Welcome to First Milk Venture API"
    }

# -----------------------------------------------------------------------------
# Authentication Endpoints
# -----------------------------------------------------------------------------
@app.post("/auth/request-otp", tags=["Authentication"])
def request_otp(data: RequestOTP):
    otp_code = f"{random.randint(100000, 999999)}"
    otp_store[data.email] = otp_code
    
    if not os.getenv("RESEND_API_KEY"):
        print(f"[DEBUG] Generated OTP for {data.email}: {otp_code}")
        return {"message": "OTP generated (Simulation Mode)", "debug_otp": otp_code}
    
    try:
        params: resend.Emails.SendParams = {
            "from": "First Milk Venture <auth@firstmilkventure.com>",
            "to": [data.email],
            "subject": "Your First Milk Venture Verification Code",
            "html": f"""
            <div style="font-family: Arial, sans-serif; padding: 20px; color: #333; max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px;">
                <h2 style="color: #2c3e50; text-align: center;">First Milk Venture</h2>
                <p>Hello,</p>
                <p>Thank you for choosing <strong>First Milk Venture</strong>—delivering pure, farm-fresh goodness straight to your doorstep.</p>
                <div style="background-color: #f8f9fa; padding: 15px; text-align: center; border-radius: 6px; margin: 20px 0;">
                    <p style="margin: 0; font-size: 14px; color: #555;">Your secure verification code is:</p>
                    <h1 style="color: #2980b9; margin: 10px 0; letter-spacing: 3px;">{otp_code}</h1>
                </div>
                <p style="font-size: 13px; color: #777;">This code is valid for a limited time. If you didn't request this, please ignore this email.</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="font-size: 12px; color: #aaa; text-align: center;">&copy; 2026 First Milk Venture. All rights reserved.</p>
            </div>
            """
        }
        email_response = resend.Emails.send(params)
        return {"message": "OTP sent successfully to your email", "response": email_response}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email OTP: {str(e)}"
        )

@app.post("/auth/verify-otp", tags=["Authentication"])
def verify_otp(data: VerifyOTP):
    stored_otp = otp_store.get(data.email)
    if not stored_otp or stored_otp != data.otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP"
        )
    
    del otp_store[data.email]
    
    return {
        "access_token": "mock_jwt_access_token_first_milk_venture",
        "token_type": "bearer",
        "message": "Authentication successful"
    }

# -----------------------------------------------------------------------------
# Dairy Products Endpoints
# -----------------------------------------------------------------------------
@app.get("/products", response_model=List[ProductResponse], tags=["Products"])
def get_products():
    if not supabase:
        return [
            {"id": "1", "name": "Farm Fresh Whole Milk", "category": "Milk", "unit": "1L", "price": 65.0, "is_available": True},
            {"id": "2", "name": "Pure Cow Ghee", "category": "Ghee", "unit": "500g", "price": 350.0, "is_available": True},
            {"id": "3", "name": "Fresh Curd", "category": "Curd", "unit": "500g", "price": 40.0, "is_available": True},
            {"id": "4", "name": "Unsalted Butter", "category": "Butter", "unit": "250g", "price": 120.0, "is_available": True}
        ]
    
    try:
        response = supabase.table("products").select("*").execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# Subscriptions Endpoints
# -----------------------------------------------------------------------------
@app.post("/subscriptions", tags=["Subscriptions"])
def create_subscription(sub: CreateSubscription):
    if not supabase:
        return {
            "message": "Subscription created (Simulation Mode)",
            "subscription": sub.dict()
        }
    
    try:
        data = {
            "user_email": sub.user_email,
            "product_id": sub.product_id,
            "quantity": sub.quantity,
            "frequency": sub.frequency,
            "start_date": sub.start_date,
            "status": "active"
        }
        response = supabase.table("subscriptions").insert(data).execute()
        return {"message": "Subscription activated successfully", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/subscriptions/{user_email}", tags=["Subscriptions"])
def get_user_subscriptions(user_email: str):
    if not supabase:
        return [{"id": "sub_1", "user_email": user_email, "product_id": "1", "quantity": 2, "frequency": "daily", "status": "active", "start_date": "2026-11-10"}]
    
    try:
        response = supabase.table("subscriptions").select("*, products(*)").eq("user_email", user_email).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# -----------------------------------------------------------------------------
# User Profile
# -----------------------------------------------------------------------------
@app.get("/users/me", tags=["Users"])
def get_me():
    return {
        "email": "user@example.com",
        "role": "customer",
        "status": "active"
    }

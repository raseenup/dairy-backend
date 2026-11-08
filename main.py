import os
import random
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
import resend

app = FastAPI(
    title="First Milk Venture API",
    description="Backend service for First Milk Venture with Email OTP authentication",
    version="1.0.0"
)

# Initialize Resend API Key
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

# In-memory OTP storage for demonstration (replace with Supabase DB model in production)
otp_store = {}

class RequestOTP(BaseModel):
    email: EmailStr

class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str

@app.get("/", tags=["General"])
def read_root():
    return {
        "status": "online",
        "company": "First Milk Venture",
        "message": "Welcome to First Milk Venture API"
    }

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

@app.get("/users/me", tags=["Users"])
def get_me():
    return {
        "email": "user@example.com",
        "role": "customer",
        "status": "active"
    }

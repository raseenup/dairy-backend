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
        # Simulated OTP log if API key not configured yet
        print(f"[DEBUG] Generated OTP for {data.email}: {otp_code}")
        return {"message": "OTP generated (Simulation Mode)", "debug_otp": otp_code}
    
    try:
        params: resend.Emails.SendParams = {
            "from": "First Milk Venture <onboarding@resend.dev>",
            "to": [data.email],
            "subject": "Your First Milk Venture Verification Code",
            "html": f"<h3>Welcome to First Milk Venture</h3><p>Your verification code is: <strong>{otp_code}</strong></p>"
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
    
    # Clear OTP after successful verification
    del otp_store[data.email]
    
    # Return simulated JWT Access Token
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

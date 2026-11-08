import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import jwt

# ---------------------------------------------------------
# Database Setup & Engine Configuration
# ---------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Auto-fix legacy 'postgres://' URLs provided by database providers for SQLAlchemy compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is missing or empty.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------
# Database Models
# ---------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, default="user", nullable=False)
    otp = Column(String, nullable=True)
    otp_expiry = Column(DateTime, nullable=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------
# Security & JWT Configuration
# ---------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "first_milk_venture_secure_secret_key_2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

security = HTTPBearer()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        phone_number: str = payload.get("sub")
        if phone_number is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

# ---------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------
class RequestOTP(BaseModel):
    phone_number: str

class VerifyOTP(BaseModel):
    phone_number: str
    otp: str

# ---------------------------------------------------------
# FastAPI App & Endpoints
# ---------------------------------------------------------
app = FastAPI(
    title="First Milk Venture API",
    version="1.0.0",
    description="Backend service for First Milk Venture"
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def seed_admin_user():
    db = SessionLocal()
    try:
        admin_phone = "+918086002624"
        admin = db.query(User).filter(User.phone_number == admin_phone).first()
        if not admin:
            admin_user = User(
                phone_number=admin_phone,
                role="admin",
                is_verified=True
            )
            db.add(admin_user)
            db.commit()
    finally:
        db.close()

@app.get("/")
def read_root():
    return {
        "status": "online",
        "company": "First Milk Venture",
        "message": "Welcome to First Milk Venture API"
    }

@app.post("/auth/request-otp")
def request_otp(payload: RequestOTP, db: Session = Depends(get_db)):
    phone = payload.phone_number.strip()
    user = db.query(User).filter(User.phone_number == phone).first()
    
    # Hardcoded test OTP for immediate verification
    generated_otp = "123456"
    expiry = datetime.utcnow() + timedelta(minutes=10)
    
    if not user:
        user = User(phone_number=phone, otp=generated_otp, otp_expiry=expiry)
        db.add(user)
    else:
        user.otp = generated_otp
        user.otp_expiry = expiry
        
    db.commit()
    return {"message": "OTP sent successfully", "test_otp": generated_otp}

@app.post("/auth/verify-otp")
def verify_otp(payload: VerifyOTP, db: Session = Depends(get_db)):
    phone = payload.phone_number.strip()
    user = db.query(User).filter(User.phone_number == phone).first()
    
    if not user or user.otp != payload.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
        
    if user.otp_expiry and datetime.utcnow() > user.otp_expiry:
        raise HTTPException(status_code=400, detail="OTP expired")
        
    user.is_verified = True
    user.otp = None
    user.otp_expiry = None
    db.commit()
    
    access_token = create_access_token(data={"sub": user.phone_number, "role": user.role})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "phone_number": user.phone_number,
            "role": user.role,
            "is_verified": user.is_verified
        }
    }

@app.get("/users/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}

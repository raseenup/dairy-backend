import os
from datetime import date, datetime, timedelta
from typing import Optional, List
import random

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    ForeignKey,
    DateTime,
    Boolean
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
import jwt

# ------------------------------------------------------------------
# 1. DATABASE & SECURITY CONFIGURATION
# ------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./milkmart.db")
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-milkmart-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

security_scheme = HTTPBearer()

# ------------------------------------------------------------------
# 2. DATABASE MODELS
# ------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False, default="First Milk Venture")
    phone = Column(String, unique=True, index=True, nullable=False)
    company_name = Column(String, nullable=False, default="First Milk Venture")
    role = Column(String, default="admin")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OTPRecord(Base):
    __tablename__ = "otp_records"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    otp_code = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_used = Column(Boolean, default=False)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)

    deliveries = relationship("Delivery", back_populates="customer", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="customer", cascade="all, delete-orphan")


class Delivery(Base):
    __tablename__ = "deliveries"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    delivery_date = Column(String, nullable=False)  # YYYY-MM-DD
    milk_type = Column(String, nullable=False)
    litres = Column(Float, nullable=False)
    rate = Column(Float, nullable=False)

    customer = relationship("Customer", back_populates="deliveries")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    bill_month = Column(String, nullable=False)  # YYYY-MM
    payment_date = Column(String, nullable=False)  # YYYY-MM-DD
    amount = Column(Float, nullable=False)
    note = Column(String, nullable=True)

    customer = relationship("Customer", back_populates="payments")


Base.metadata.create_all(bind=engine)

# Auto-seed primary admin user for First Milk Venture (+918086002624)
def seed_initial_user():
    db = SessionLocal()
    try:
        admin_phone = "+918086002624"
        user = db.query(User).filter(User.phone == admin_phone).first()
        if not user:
            default_user = User(
                full_name="First Milk Venture",
                phone=admin_phone,
                company_name="First Milk Venture",
                role="admin",
                is_active=True
            )
            db.add(default_user)
            db.commit()
    finally:
        db.close()

seed_initial_user()

# ------------------------------------------------------------------
# 3. SCHEMAS
# ------------------------------------------------------------------
class UserCreate(BaseModel):
    full_name: str = "First Milk Venture"
    phone: str = "+918086002624"
    company_name: Optional[str] = "First Milk Venture"

class RequestOTP(BaseModel):
    phone: str = "+918086002624"

class VerifyOTP(BaseModel):
    phone: str = "+918086002624"
    otp_code: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    full_name: str
    phone: str
    company_name: str

class CustomerCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None

class CustomerResponse(CustomerCreate):
    id: int
    class Config:
        from_attributes = True

class DeliveryCreate(BaseModel):
    customer_id: int
    delivery_date: str
    milk_type: str = "Cow milk"
    litres: float
    rate: float

class DeliveryResponse(DeliveryCreate):
    id: int
    total_amount: float
    customer_name: Optional[str] = None
    class Config:
        from_attributes = True

class PaymentCreate(BaseModel):
    customer_id: int
    bill_month: str
    amount: float
    note: Optional[str] = None

class PaymentResponse(PaymentCreate):
    id: int
    payment_date: str
    class Config:
        from_attributes = True

class BillSummary(BaseModel):
    customer_id: int
    customer_name: str
    total_litres: float
    bill_amount: float
    paid_amount: float
    balance: float

# ------------------------------------------------------------------
# 4. APP & AUTH DEPENDENCIES
# ------------------------------------------------------------------
app = FastAPI(
    title="First Milk Venture API",
    description="Backend service for First Milk Venture - Milk Delivery & Billing Management",
    version="2.2.0"
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_jwt_token(user_id: int, phone: str) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "phone": phone, "company": "First Milk Venture", "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: Session = Depends(get_db)
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or not found")
        return user
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ------------------------------------------------------------------
# 5. USER REGISTRATION & OTP AUTH ENDPOINTS
# ------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "company": "First Milk Venture",
        "status": "online",
        "message": "First Milk Venture API (OTP Secured) is running!"
    }


@app.post("/auth/register-user/")
def register_user(user_data: UserCreate, db: Session = Depends(get_db)):
    phone = user_data.phone.strip()
    full_name = user_data.full_name.strip()
    company_name = (user_data.company_name or "First Milk Venture").strip()

    if not phone or not full_name:
        raise HTTPException(status_code=400, detail="Full name and phone number are required.")

    existing_user = db.query(User).filter(User.phone == phone).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Phone number already registered. Please request OTP to log in.")

    otp_code = f"{random.randint(100000, 999999)}"
    otp_entry = OTPRecord(phone=phone, otp_code=otp_code)
    
    temp_user = User(full_name=full_name, phone=phone, company_name=company_name, is_active=False)
    db.add(temp_user)
    db.add(otp_entry)
    db.commit()

    return {
        "company": company_name,
        "message": "User registered successfully. Please verify OTP to activate account.",
        "phone": phone,
        "dev_note": f"For testing, your OTP is: {otp_code}"
    }


@app.post("/auth/request-otp/")
def request_otp(payload: RequestOTP, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required.")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")

    otp_code = f"{random.randint(100000, 999999)}"
    otp_entry = OTPRecord(phone=phone, otp_code=otp_code)
    db.add(otp_entry)
    db.commit()

    return {
        "company": user.company_name,
        "message": "OTP generated successfully.",
        "phone": phone,
        "dev_note": f"For testing, your OTP is: {otp_code}"
    }


@app.post("/auth/verify-otp/", response_model=TokenResponse)
def verify_otp(payload: VerifyOTP, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    otp_input = payload.otp_code.strip()

    ten_mins_ago = datetime.utcnow() - timedelta(minutes=10)
    record = db.query(OTPRecord).filter(
        OTPRecord.phone == phone,
        OTPRecord.otp_code == otp_input,
        OTPRecord.is_used == False,
        OTPRecord.created_at >= ten_mins_ago
    ).order_by(OTPRecord.id.desc()).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    record.is_used = True
    
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User record not found.")

    user.is_active = True
    db.commit()

    token = create_jwt_token(user.id, user.phone)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        full_name=user.full_name,
        phone=user.phone,
        company_name=user.company_name
    )

# ------------------------------------------------------------------
# 6. PROTECTED ENDPOINTS
# ------------------------------------------------------------------

@app.post("/customers/", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not customer.name.strip():
        raise HTTPException(status_code=400, detail="Customer name cannot be empty.")
    
    db_customer = Customer(
        name=customer.name.strip(),
        phone=customer.phone.strip() if customer.phone else None,
        address=customer.address.strip() if customer.address else None
    )
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer


@app.get("/customers/", response_model=List[CustomerResponse])
def get_customers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(Customer).order_by(Customer.name).all()


@app.post("/deliveries/", response_model=DeliveryResponse, status_code=status.HTTP_201_CREATED)
def create_delivery(
    delivery: DeliveryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    customer = db.query(Customer).filter(Customer.id == delivery.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    
    if delivery.litres <= 0 or delivery.rate < 0:
        raise HTTPException(status_code=400, detail="Litres must be > 0 and rate cannot be negative.")

    db_delivery = Delivery(
        customer_id=delivery.customer_id,
        delivery_date=delivery.delivery_date,
        milk_type=delivery.milk_type,
        litres=delivery.litres,
        rate=delivery.rate
    )
    db.add(db_delivery)
    db.commit()
    db.refresh(db_delivery)

    return DeliveryResponse(
        id=db_delivery.id,
        customer_id=db_delivery.customer_id,
        delivery_date=db_delivery.delivery_date,
        milk_type=db_delivery.milk_type,
        litres=db_delivery.litres,
        rate=db_delivery.rate,
        total_amount=round(db_delivery.litres * db_delivery.rate, 2),
        customer_name=customer.name
    )


@app.get("/deliveries/", response_model=List[DeliveryResponse])
def get_deliveries(
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    deliveries = db.query(Delivery).order_by(Delivery.delivery_date.desc(), Delivery.id.desc()).limit(limit).all()
    results = []
    for d in deliveries:
        results.append(DeliveryResponse(
            id=d.id,
            customer_id=d.customer_id,
            delivery_date=d.delivery_date,
            milk_type=d.milk_type,
            litres=d.litres,
            rate=d.rate,
            total_amount=round(d.litres * d.rate, 2),
            customer_name=d.customer.name if d.customer else "Unknown"
        ))
    return results


@app.post("/payments/", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_payment(
    payment: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    customer = db.query(Customer).filter(Customer.id == payment.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    if payment.amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than zero.")

    db_payment = Payment(
        customer_id=payment.customer_id,
        bill_month=payment.bill_month,
        payment_date=date.today().isoformat(),
        amount=payment.amount,
        note=payment.note
    )
    db.add(db_payment)
    db.commit()
    db.refresh(db_payment)

    return db_payment


@app.get("/bills/{month}", response_model=List[BillSummary])
def get_monthly_bills(
    month: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    customers = db.query(Customer).order_by(Customer.name).all()
    summaries = []

    for c in customers:
        deliveries = db.query(Delivery).filter(
            Delivery.customer_id == c.id,
            Delivery.delivery_date.like(f"{month}%")
        ).all()
        
        tot_litres = sum(d.litres for d in deliveries)
        tot_bill = sum(d.litres * d.rate for d in deliveries)

        payments = db.query(Payment).filter(
            Payment.customer_id == c.id,
            Payment.bill_month == month
        ).all()
        
        tot_paid = sum(p.amount for p in payments)
        balance = tot_bill - tot_paid

        summaries.append(BillSummary(
            customer_id=c.id,
            customer_name=c.name,
            total_litres=round(tot_litres, 2),
            bill_amount=round(tot_bill, 2),
            paid_amount=round(tot_paid, 2),
            balance=round(balance, 2)
        ))

    return summaries

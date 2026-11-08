import os
from datetime import date
from typing import Optional, List
from decimal import Decimal

from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    ForeignKey,
    Numeric,
    func,
    select
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# ------------------------------------------------------------------
# 1. DATABASE CONNECTION SETUP
# ------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./milkmart.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ------------------------------------------------------------------
# 2. DATABASE MODELS
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 3. PYDANTIC SCHEMAS
# ------------------------------------------------------------------
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
# 4. FASTAPI APP & DEPENDENCIES
# ------------------------------------------------------------------
app = FastAPI(
    title="MilkMart API",
    description="Backend service for Milk Delivery & Billing Management",
    version="1.0.0"
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------------------------------------------------------
# 5. API ENDPOINTS
# ------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "online", "message": "MilkMart FastAPI Backend is running!"}

# --- CUSTOMERS ---

@app.post("/customers/", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer(customer: CustomerCreate, db: Session = Depends(get_db)):
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
def get_customers(db: Session = Depends(get_db)):
    return db.query(Customer).order_by(Customer.name).all()

# --- DELIVERIES ---

@app.post("/deliveries/", response_model=DeliveryResponse, status_code=status.HTTP_201_CREATED)
def create_delivery(delivery: DeliveryCreate, db: Session = Depends(get_db)):
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
def get_deliveries(limit: int = 100, db: Session = Depends(get_db)):
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

# --- PAYMENTS ---

@app.post("/payments/", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_payment(payment: PaymentCreate, db: Session = Depends(get_db)):
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

# --- MONTHLY BILLS & BALANCES ---

@app.get("/bills/{month}", response_model=List[BillSummary])
def get_monthly_bills(month: str, db: Session = Depends(get_db)):
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

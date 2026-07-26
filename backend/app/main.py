from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from .config import settings
from .database import Base, engine, get_db
from .models import AuditLog, Business, BusinessUser, Role, Store, Subscription, User
from .schemas import BusinessOut, OTPRequest, OTPVerify, OnboardingRequest, Token
from .security import create_token, current_user
from .operations import router as operations_router

app = FastAPI(title=settings.app_name, version="0.1.0", description="Milestone 1 foundation API")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:3000","http://localhost:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
Base.metadata.create_all(engine)
if "payment_mode" not in {column["name"] for column in inspect(engine).get_columns("purchases")}:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE purchases ADD COLUMN payment_mode VARCHAR(20) NOT NULL DEFAULT 'cash'"))
app.include_router(operations_router)

@app.get("/api/health")
def health(): return {"status": "ok", "service": settings.app_name}

@app.post("/api/auth/otp/request", status_code=status.HTTP_202_ACCEPTED)
def request_otp(body: OTPRequest):
    return {"message": "OTP sent", "expires_in": settings.otp_ttl_seconds}

@app.post("/api/auth/otp/verify", response_model=Token)
def verify_otp(body: OTPVerify, db: Session = Depends(get_db)):
    if body.code != settings.otp_dev_code: raise HTTPException(400, "Incorrect OTP")
    user = db.scalar(select(User).where(User.mobile == body.mobile))
    if not user:
        user = User(mobile=body.mobile, name=body.name); db.add(user); db.commit(); db.refresh(user)
    elif body.name.strip() and user.name != body.name.strip():
        user.name=body.name.strip(); db.commit(); db.refresh(user)
    return Token(access_token=create_token(user.id))

@app.post("/api/onboarding", response_model=BusinessOut, status_code=201)
def onboard(body: OnboardingRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.scalar(select(BusinessUser).where(BusinessUser.user_id == user.id, BusinessUser.active.is_(True)))
    if existing: raise HTTPException(409, "User already belongs to an active business")
    business = Business(name=body.business_name, preferred_language=body.preferred_language, onboarding_complete=True)
    db.add(business); db.flush()
    membership = BusinessUser(business_id=business.id, user_id=user.id, role=Role.OWNER)
    store = Store(business_id=business.id, name=body.store_name, city=body.city, state=body.state, pin_code=body.pin_code)
    db.add_all([membership, store, Subscription(business_id=business.id), AuditLog(business_id=business.id, user_id=user.id, action="create", entity="business", record_id=business.id)])
    db.commit()
    return BusinessOut(id=business.id, name=business.name, role=membership.role.value, onboarding_complete=True)

@app.get("/api/businesses", response_model=list[BusinessOut])
def my_businesses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Business, BusinessUser).join(BusinessUser).where(BusinessUser.user_id == user.id, BusinessUser.active.is_(True))).all()
    return [BusinessOut(id=b.id, name=b.name, role=m.role.value, onboarding_complete=b.onboarding_complete) for b,m in rows]

@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "mobile": user.mobile}

@app.get("/api/businesses/{business_id}", response_model=BusinessOut)
def business(business_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.execute(select(Business, BusinessUser).join(BusinessUser).where(Business.id == business_id, BusinessUser.user_id == user.id, BusinessUser.active.is_(True))).first()
    if not row: raise HTTPException(404, "Business not found")
    b,m = row
    return BusinessOut(id=b.id, name=b.name, role=m.role.value, onboarding_complete=b.onboarding_complete)

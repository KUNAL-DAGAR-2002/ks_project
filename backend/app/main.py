from datetime import datetime, timedelta, timezone
import math
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session
from .config import settings
from .database import Base, engine, get_db
from .models import AIUsageEvent, AuditLog, Business, BusinessUser, Customer, Expense, ImageDocument, InventoryMovement, Product, Purchase, Role, Sale, Store, Subscription, Supplier, UdhaarEntry, User
from .schemas import AdminLogin, BusinessOut, EmailLogin, EmailSignup, OTPRequest, OTPVerify, OnboardingRequest, Token
from .security import create_admin_token, create_token, current_admin, current_user, hash_password, verify_password
from .operations import router as operations_router

app = FastAPI(title=settings.app_name, version="0.1.0", description="Milestone 1 foundation API")
app.add_middleware(CORSMiddleware,allow_origins=settings.allowed_origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
Base.metadata.create_all(engine)
user_columns={column["name"] for column in inspect(engine).get_columns("users")}
if "email" not in user_columns:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(254)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
if "password_hash" not in user_columns:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
if "payment_mode" not in {column["name"] for column in inspect(engine).get_columns("purchases")}:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE purchases ADD COLUMN payment_mode VARCHAR(20) NOT NULL DEFAULT 'cash'"))
if "udhaar_entries" in inspect(engine).get_table_names() and "total_present" not in {column["name"] for column in inspect(engine).get_columns("udhaar_entries")}:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE udhaar_entries ADD COLUMN total_present BOOLEAN NOT NULL DEFAULT 1"))
if "cost_known" not in {column["name"] for column in inspect(engine).get_columns("sale_lines")}:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE sale_lines ADD COLUMN cost_known BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("UPDATE sale_lines SET cost_known = CASE WHEN unit_cost > 0 THEN TRUE ELSE FALSE END"))
subscription_columns={column["name"] for column in inspect(engine).get_columns("subscriptions")}
with engine.begin() as connection:
    if "monthly_price" not in subscription_columns:
        connection.execute(text("ALTER TABLE subscriptions ADD COLUMN monthly_price FLOAT NOT NULL DEFAULT 599"))
    if "started_at" not in subscription_columns:
        connection.execute(text("ALTER TABLE subscriptions ADD COLUMN started_at DATETIME"))
    if "ends_at" not in subscription_columns:
        connection.execute(text("ALTER TABLE subscriptions ADD COLUMN ends_at DATETIME"))
app.include_router(operations_router)

def subscription_snapshot(subscription:Subscription|None):
    now=datetime.now(timezone.utc)
    if not subscription:
        return {"plan":"starter","status":"inactive","monthly_price":599,"started_at":None,"ends_at":None,
                "access_active":False,"days_remaining":0,"can_start_trial":True}
    ends=subscription.ends_at
    if ends and ends.tzinfo is None:ends=ends.replace(tzinfo=timezone.utc)
    access_active=subscription.status in {"active","trial"} and bool(ends and ends>now)
    days=max(0,math.ceil((ends-now).total_seconds()/86400)) if access_active and ends else 0
    effective_status=subscription.status if access_active else ("expired" if subscription.status in {"active","trial"} else subscription.status)
    return {"plan":subscription.plan,"status":effective_status,"monthly_price":subscription.monthly_price,
            "started_at":subscription.started_at,"ends_at":subscription.ends_at,"access_active":access_active,
            "days_remaining":days,"can_start_trial":subscription.started_at is None}

@app.get("/api/health")
def health(): return {"status": "ok", "service": settings.app_name}

@app.post("/api/auth/otp/request", status_code=status.HTTP_202_ACCEPTED)
def request_otp(body: OTPRequest):
    return {"message": "OTP sent", "expires_in": settings.otp_ttl_seconds}

@app.post("/api/auth/otp/verify", response_model=Token)
def verify_otp(body: OTPVerify, db: Session = Depends(get_db)):
    if body.code != settings.otp_dev_code: raise HTTPException(400, "Incorrect OTP")
    user = db.scalar(select(User).where(User.mobile == body.mobile))
    if body.intent == "login":
        if not user:
            raise HTTPException(404, "Account not found. Please sign up first.")
    elif body.intent == "signup":
        if user:
            raise HTTPException(409, "Account already exists. Please log in instead.")
        user = User(mobile=body.mobile, name=body.name.strip())
        db.add(user); db.commit(); db.refresh(user)
    elif not user:
        # Backwards compatibility for existing API clients and seed utilities.
        if not body.name or len(body.name.strip()) < 2:
            raise HTTPException(422, "Name is required to create an account")
        user = User(mobile=body.mobile, name=body.name.strip())
        db.add(user); db.commit(); db.refresh(user)
    elif body.intent == "legacy" and body.name and user.name != body.name.strip():
        user.name = body.name.strip()
        db.commit(); db.refresh(user)
    return Token(access_token=create_token(user.id))

@app.post("/api/auth/signup",response_model=Token,status_code=201)
def email_signup(body:EmailSignup,db:Session=Depends(get_db)):
    email=str(body.email).strip().lower()
    if db.scalar(select(User.id).where(func.lower(User.email)==email)):raise HTTPException(409,"An account already exists for this email. Please log in.")
    user=User(mobile=f"E{__import__('uuid').uuid4().hex[:14]}",email=email,password_hash=hash_password(body.password),name=body.name.strip())
    db.add(user);db.flush()
    business_name=(body.business_name or f"{body.name.strip()}'s Store").strip()
    business=Business(name=business_name,preferred_language=body.preferred_language,onboarding_complete=True)
    db.add(business);db.flush()
    membership=BusinessUser(business_id=business.id,user_id=user.id,role=Role.OWNER)
    store=Store(business_id=business.id,name=business_name,city=body.city.strip(),state="Not set",pin_code=body.pin_code)
    db.add_all([membership,store,Subscription(business_id=business.id,plan="starter",status="inactive",monthly_price=599),
                AuditLog(business_id=business.id,user_id=user.id,action="create",entity="business",record_id=business.id)])
    db.commit();db.refresh(user)
    return Token(access_token=create_token(user.id))

@app.post("/api/auth/login",response_model=Token)
def email_login(body:EmailLogin,db:Session=Depends(get_db)):
    user=db.scalar(select(User).where(func.lower(User.email)==str(body.email).strip().lower()))
    if not user or not verify_password(body.password,user.password_hash):raise HTTPException(401,"Incorrect email or password")
    if not user.is_active:raise HTTPException(403,"This account is inactive")
    return Token(access_token=create_token(user.id))

@app.post("/api/admin/login",response_model=Token)
def admin_login(body:AdminLogin):
    import secrets
    if not secrets.compare_digest(body.username,settings.admin_username) or not secrets.compare_digest(body.password,settings.admin_password):
        raise HTTPException(401,"Incorrect admin ID or password")
    return Token(access_token=create_admin_token(body.username))

@app.get("/api/admin/overview")
def admin_overview(_:str=Depends(current_admin),db:Session=Depends(get_db)):
    users=db.scalars(select(User).order_by(User.created_at.desc())).all()
    result=[]
    count_models=[Product,InventoryMovement,Sale,Purchase,Customer,Supplier,UdhaarEntry,Expense,ImageDocument]
    for user in users:
        memberships=db.execute(select(BusinessUser,Business).join(Business,Business.id==BusinessUser.business_id).where(BusinessUser.user_id==user.id)).all()
        businesses=[]
        total_records=0
        for membership,business in memberships:
            counts={model.__tablename__:int(db.scalar(select(func.count()).select_from(model).where(model.business_id==business.id)) or 0) for model in count_models}
            record_count=sum(counts.values());total_records+=record_count
            subscription=db.scalar(select(Subscription).where(Subscription.business_id==business.id))
            businesses.append({"id":business.id,"name":business.name,"role":membership.role.value,"records":record_count,"counts":counts,
                "subscription":subscription_snapshot(subscription)})
        ai=db.execute(select(func.count(AIUsageEvent.id),func.coalesce(func.sum(AIUsageEvent.total_tokens),0)).where(AIUsageEvent.user_id==user.id)).one()
        result.append({"id":user.id,"name":user.name,"email":user.email,"active":user.is_active,"created_at":user.created_at,
                       "businesses":businesses,"data_records":total_records,"ai_requests":int(ai[0] or 0),"ai_tokens":int(ai[1] or 0)})
    daily=db.execute(select(func.date(AuditLog.created_at),func.count(AuditLog.id)).group_by(func.date(AuditLog.created_at)).order_by(func.date(AuditLog.created_at).desc()).limit(30)).all()
    ai_daily=db.execute(select(func.date(AIUsageEvent.created_at),func.count(AIUsageEvent.id),func.coalesce(func.sum(AIUsageEvent.total_tokens),0)).group_by(func.date(AIUsageEvent.created_at)).order_by(func.date(AIUsageEvent.created_at).desc()).limit(30)).all()
    ai_map={str(day):{"ai_requests":int(requests),"ai_tokens":int(tokens or 0)} for day,requests,tokens in ai_daily}
    daily_usage=[{"date":str(day),"data_actions":int(actions),**ai_map.get(str(day),{"ai_requests":0,"ai_tokens":0})} for day,actions in daily]
    return {"users":result,"summary":{"users":len(users),"businesses":int(db.scalar(select(func.count()).select_from(Business)) or 0),
            "data_records":sum(row["data_records"] for row in result),"ai_requests":sum(row["ai_requests"] for row in result),
            "ai_tokens":sum(row["ai_tokens"] for row in result)},"daily_usage":daily_usage}

@app.post("/api/admin/users/{user_id}/subscription")
def grant_subscription(user_id:str,_:str=Depends(current_admin),db:Session=Depends(get_db)):
    user=db.get(User,user_id)
    if not user:raise HTTPException(404,"User not found")
    memberships=db.scalars(select(BusinessUser).where(BusinessUser.user_id==user_id,BusinessUser.active.is_(True))).all()
    if not memberships:raise HTTPException(400,"This user has no active store")
    now=datetime.now(timezone.utc);started=now;ends=now+timedelta(days=30)
    for membership in memberships:
        subscription=db.scalar(select(Subscription).where(Subscription.business_id==membership.business_id))
        if not subscription:
            subscription=Subscription(business_id=membership.business_id)
            db.add(subscription)
        current_end=subscription.ends_at
        if current_end and current_end.tzinfo is None:current_end=current_end.replace(tzinfo=timezone.utc)
        if subscription.status in {"active","trial"} and current_end and current_end>now:
            ends=current_end+timedelta(days=30)
            started=subscription.started_at or now
        subscription.plan="starter";subscription.status="active";subscription.monthly_price=599
        subscription.started_at=started;subscription.ends_at=ends
    db.commit()
    return {"message":"Starter membership activated","started_at":started,"ends_at":ends}

@app.delete("/api/admin/users/{user_id}/subscription")
def end_subscription(user_id:str,_:str=Depends(current_admin),db:Session=Depends(get_db)):
    if not db.get(User,user_id):raise HTTPException(404,"User not found")
    business_ids=db.scalars(select(BusinessUser.business_id).where(BusinessUser.user_id==user_id)).all()
    subscriptions=db.scalars(select(Subscription).where(Subscription.business_id.in_(business_ids))).all() if business_ids else []
    ended=datetime.now(timezone.utc)
    for subscription in subscriptions:
        subscription.status="ended";subscription.ends_at=ended
    db.commit()
    return {"message":"Membership ended","ended_at":ended}

@app.get("/api/businesses/{business_id}/subscription")
def my_subscription(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    membership=db.scalar(select(BusinessUser).where(BusinessUser.business_id==business_id,BusinessUser.user_id==user.id,BusinessUser.active.is_(True)))
    if not membership:raise HTTPException(404,"Business not found")
    return subscription_snapshot(db.scalar(select(Subscription).where(Subscription.business_id==business_id)))

@app.post("/api/businesses/{business_id}/subscription/trial")
def start_trial(business_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    membership=db.scalar(select(BusinessUser).where(BusinessUser.business_id==business_id,BusinessUser.user_id==user.id,BusinessUser.active.is_(True)))
    if not membership or membership.role!=Role.OWNER:raise HTTPException(403,"Only the store owner can start a trial")
    subscription=db.scalar(select(Subscription).where(Subscription.business_id==business_id))
    if not subscription:
        subscription=Subscription(business_id=business_id)
        db.add(subscription)
    if subscription.started_at is not None:raise HTTPException(409,"The free trial has already been used for this store")
    started=datetime.now(timezone.utc)
    subscription.plan="starter";subscription.status="trial";subscription.monthly_price=599
    subscription.started_at=started;subscription.ends_at=started+timedelta(days=7)
    db.commit();db.refresh(subscription)
    return subscription_snapshot(subscription)

@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id:str,_:str=Depends(current_admin),db:Session=Depends(get_db)):
    user=db.get(User,user_id)
    if not user:raise HTTPException(404,"User not found")
    memberships=db.scalars(select(BusinessUser).where(BusinessUser.user_id==user_id)).all()
    business_ids=[membership.business_id for membership in memberships]
    for business_id in business_ids:
        member_count=int(db.scalar(select(func.count()).select_from(BusinessUser).where(BusinessUser.business_id==business_id)) or 0)
        if member_count>1:
            raise HTTPException(409,"Cannot delete a user attached to a shared business")
    # Delete tenant data in reverse dependency order, followed by the account.
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in {"users","businesses","product_unit_map"} or "business_id" not in table.c:
            continue
        db.execute(table.delete().where(table.c.business_id.in_(business_ids)))
    if business_ids:
        db.execute(Business.__table__.delete().where(Business.id.in_(business_ids)))
    db.execute(User.__table__.delete().where(User.id==user_id))
    db.commit()
    return {"message":"User and owned business data deleted"}

@app.post("/api/onboarding", response_model=BusinessOut, status_code=201)
def onboard(body: OnboardingRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.scalar(select(BusinessUser).where(BusinessUser.user_id == user.id, BusinessUser.active.is_(True)))
    if existing: raise HTTPException(409, "User already belongs to an active business")
    business = Business(name=body.business_name, preferred_language=body.preferred_language, onboarding_complete=True)
    db.add(business); db.flush()
    membership = BusinessUser(business_id=business.id, user_id=user.id, role=Role.OWNER)
    store = Store(business_id=business.id, name=body.store_name, city=body.city, state=body.state, pin_code=body.pin_code)
    db.add_all([membership, store, Subscription(business_id=business.id,plan="starter",status="inactive",monthly_price=599), AuditLog(business_id=business.id, user_id=user.id, action="create", entity="business", record_id=business.id)])
    db.commit()
    return BusinessOut(id=business.id, name=business.name, role=membership.role.value, onboarding_complete=True)

@app.get("/api/businesses", response_model=list[BusinessOut])
def my_businesses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Business, BusinessUser).join(BusinessUser).where(BusinessUser.user_id == user.id, BusinessUser.active.is_(True))).all()
    return [BusinessOut(id=b.id, name=b.name, role=m.role.value, onboarding_complete=b.onboarding_complete) for b,m in rows]

@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email}

@app.get("/api/businesses/{business_id}", response_model=BusinessOut)
def business(business_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.execute(select(Business, BusinessUser).join(BusinessUser).where(Business.id == business_id, BusinessUser.user_id == user.id, BusinessUser.active.is_(True))).first()
    if not row: raise HTTPException(404, "Business not found")
    b,m = row
    return BusinessOut(id=b.id, name=b.name, role=m.role.value, onboarding_complete=b.onboarding_complete)

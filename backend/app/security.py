from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import User

bearer = HTTPBearer()
password_hasher = PasswordHash.recommended()

def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)}, settings.jwt_secret, algorithm="HS256")

def hash_password(password: str) -> str:
    return password_hasher.hash(password)

def verify_password(password: str, encoded: str | None) -> bool:
    return bool(encoded) and password_hasher.verify(password, encoded)

def create_admin_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": username, "scope": "admin", "iat": now, "exp": now + timedelta(hours=8)}, settings.jwt_secret, algorithm="HS256")

def current_admin(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired admin session") from exc
    if payload.get("scope") != "admin" or payload.get("sub") != settings.admin_username:
        raise HTTPException(403, "Admin access required")
    return str(payload["sub"])

def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired token") from exc
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active: raise HTTPException(401, "User is unavailable")
    return user

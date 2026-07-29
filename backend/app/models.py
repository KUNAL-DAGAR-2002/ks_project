import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def uid() -> str: return str(uuid.uuid4())
def now() -> datetime: return datetime.now(timezone.utc)

class Role(str, enum.Enum):
    OWNER = "owner"
    MANAGER = "manager"
    STAFF = "staff"
    PLATFORM_ADMIN = "platform_admin"

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    mobile: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memberships: Mapped[list["BusinessUser"]] = relationship(back_populates="user")

class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160), index=True)
    preferred_language: Mapped[str] = mapped_column(String(10), default="en")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memberships: Mapped[list["BusinessUser"]] = relationship(back_populates="business")
    stores: Mapped[list["Store"]] = relationship(back_populates="business")

class BusinessUser(Base):
    __tablename__ = "business_users"
    __table_args__ = (UniqueConstraint("business_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.OWNER)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    user: Mapped[User] = relationship(back_populates="memberships")
    business: Mapped[Business] = relationship(back_populates="memberships")

class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(80))
    pin_code: Mapped[str] = mapped_column(String(6))
    business: Mapped[Business] = relationship(back_populates="stores")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(80))
    record_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    feature: Mapped[str] = mapped_column(String(60))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

class Category(Base):
    __tablename__ = "categories"; __table_args__ = (UniqueConstraint("business_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(100)); gst_rate: Mapped[float] = mapped_column(Float, default=0); active: Mapped[bool] = mapped_column(Boolean, default=True)

class Supplier(Base):
    __tablename__ = "suppliers"; __table_args__ = (UniqueConstraint("business_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(160)); mobile: Mapped[str | None] = mapped_column(String(15)); gstin: Mapped[str | None] = mapped_column(String(15)); lead_time_days: Mapped[int] = mapped_column(Integer, default=2); active: Mapped[bool] = mapped_column(Boolean, default=True)

class Customer(Base):
    __tablename__ = "customers"; __table_args__ = (UniqueConstraint("business_id", "mobile"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(160)); mobile: Mapped[str | None] = mapped_column(String(15)); credit_limit: Mapped[float] = mapped_column(Float, default=0); active: Mapped[bool] = mapped_column(Boolean, default=True)

class Product(Base):
    __tablename__ = "products"; __table_args__ = (UniqueConstraint("business_id", "code"), UniqueConstraint("business_id", "barcode"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id")); preferred_supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id")); code: Mapped[str] = mapped_column(String(30)); name: Mapped[str] = mapped_column(String(180)); local_name: Mapped[str | None] = mapped_column(String(180)); brand: Mapped[str | None] = mapped_column(String(100)); barcode: Mapped[str | None] = mapped_column(String(40)); base_unit: Mapped[str] = mapped_column(String(20), default="piece"); purchase_unit: Mapped[str] = mapped_column(String(20), default="piece"); selling_unit: Mapped[str] = mapped_column(String(20), default="piece"); conversion_factor: Mapped[float] = mapped_column(Float, default=1); mrp: Mapped[float] = mapped_column(Float, default=0); selling_price: Mapped[float] = mapped_column(Float, default=0); purchase_cost: Mapped[float] = mapped_column(Float, default=0); reorder_level: Mapped[float] = mapped_column(Float, default=0); safety_stock: Mapped[float] = mapped_column(Float, default=0); active: Mapped[bool] = mapped_column(Boolean, default=True)

class ProductAlias(Base):
    __tablename__ = "product_aliases"; __table_args__ = (UniqueConstraint("business_id", "alias"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True); alias: Mapped[str] = mapped_column(String(180))

class ProductUnitMap(Base):
    """Shared one-time research cache for Indian kirana selling units."""
    __tablename__ = "product_unit_map"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    normalized_name: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(180))
    selling_unit: Mapped[str] = mapped_column(String(20), default="piece")
    acceptable_units: Mapped[list] = mapped_column(JSON, default=list)
    reasoning: Mapped[str | None] = mapped_column(String(500))
    sources: Mapped[list] = mapped_column(JSON, default=list)
    lookup_status: Mapped[str] = mapped_column(String(20), default="researched")
    researched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True); product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True); movement_type: Mapped[str] = mapped_column(String(30)); quantity_base: Mapped[float] = mapped_column(Float); unit_cost: Mapped[float] = mapped_column(Float, default=0); reference_type: Mapped[str | None] = mapped_column(String(30)); reference_id: Mapped[str | None] = mapped_column(String(36)); transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now); created_by: Mapped[str] = mapped_column(ForeignKey("users.id")); notes: Mapped[str | None] = mapped_column(Text)

class Sale(Base):
    __tablename__ = "sales"; __table_args__ = (UniqueConstraint("business_id", "invoice_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); store_id: Mapped[str] = mapped_column(ForeignKey("stores.id")); invoice_number: Mapped[str] = mapped_column(String(40)); customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id")); payment_mode: Mapped[str] = mapped_column(String(20)); gross: Mapped[float] = mapped_column(Float); discount: Mapped[float] = mapped_column(Float, default=0); net: Mapped[float] = mapped_column(Float); created_by: Mapped[str] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SaleLine(Base):
    __tablename__ = "sale_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True); product_id: Mapped[str] = mapped_column(ForeignKey("products.id")); quantity: Mapped[float] = mapped_column(Float); unit_price: Mapped[float] = mapped_column(Float); unit_cost: Mapped[float] = mapped_column(Float); net: Mapped[float] = mapped_column(Float)

class Purchase(Base):
    __tablename__ = "purchases"; __table_args__ = (UniqueConstraint("business_id", "supplier_id", "invoice_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); store_id: Mapped[str] = mapped_column(ForeignKey("stores.id")); supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id")); invoice_number: Mapped[str] = mapped_column(String(60)); total: Mapped[float] = mapped_column(Float); paid: Mapped[float] = mapped_column(Float, default=0); payment_mode: Mapped[str] = mapped_column(String(20), default="cash"); status: Mapped[str] = mapped_column(String(20), default="received"); created_by: Mapped[str] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id")); product_id: Mapped[str] = mapped_column(ForeignKey("products.id")); quantity: Mapped[float] = mapped_column(Float); unit_cost: Mapped[float] = mapped_column(Float); total: Mapped[float] = mapped_column(Float)

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); party_type: Mapped[str] = mapped_column(String(20)); party_id: Mapped[str] = mapped_column(String(36), index=True); entry_type: Mapped[str] = mapped_column(String(30)); amount: Mapped[float] = mapped_column(Float); reference_id: Mapped[str | None] = mapped_column(String(36)); due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_by: Mapped[str] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class UdhaarEntry(Base):
    __tablename__ = "udhaar_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    products: Mapped[str] = mapped_column(Text, default="")
    total_present: Mapped[bool] = mapped_column(Boolean, default=True)
    amount: Mapped[float] = mapped_column(Float)
    given: Mapped[float] = mapped_column(Float, default=0)
    pending: Mapped[float] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); store_id: Mapped[str] = mapped_column(ForeignKey("stores.id")); category: Mapped[str] = mapped_column(String(80)); amount: Mapped[float] = mapped_column(Float); payment_method: Mapped[str] = mapped_column(String(20)); payee: Mapped[str | None] = mapped_column(String(120)); created_by: Mapped[str] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ImageDocument(Base):
    __tablename__ = "image_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); store_id: Mapped[str] = mapped_column(ForeignKey("stores.id")); uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id")); document_type: Mapped[str] = mapped_column(String(30)); filename: Mapped[str] = mapped_column(String(255)); storage_path: Mapped[str] = mapped_column(String(500)); mime_type: Mapped[str] = mapped_column(String(80)); status: Mapped[str] = mapped_column(String(30), default="uploaded"); quality_score: Mapped[float | None] = mapped_column(Float); confidence: Mapped[float | None] = mapped_column(Float); raw_text: Mapped[str | None] = mapped_column(Text); structured_data: Mapped[dict | None] = mapped_column(JSON); confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid); business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True); type: Mapped[str] = mapped_column(String(30)); severity: Mapped[str] = mapped_column(String(15)); entity_id: Mapped[str | None] = mapped_column(String(36)); message: Mapped[str] = mapped_column(String(500)); status: Mapped[str] = mapped_column(String(20), default="open"); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), unique=True)
    plan: Mapped[str] = mapped_column(String(30), default="starter")
    status: Mapped[str] = mapped_column(String(20), default="inactive")
    monthly_price: Mapped[float] = mapped_column(Float, default=599)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_limit: Mapped[int] = mapped_column(Integer, default=10)
    image_used: Mapped[int] = mapped_column(Integer, default=0)

from sqlalchemy import select
from app.database import Base, SessionLocal, engine
from app.models import AuditLog, Business, BusinessUser, Category, Customer, Expense, InventoryMovement, LedgerEntry, Product, Role, Store, Subscription, Supplier, User

def run():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.mobile == "9876543210"))
        if owner:
            member=db.scalar(select(BusinessUser).where(BusinessUser.user_id==owner.id)); business=db.get(Business,member.business_id); store=db.scalar(select(Store).where(Store.business_id==business.id))
            if db.scalar(select(Product.id).where(Product.business_id==business.id)):
                print("Complete demo data already seeded"); return
        else:
            owner = User(mobile="9876543210", name="Rajesh Gupta")
            business = Business(name="Gupta Kirana Store", preferred_language="hinglish", onboarding_complete=True)
            db.add_all([owner, business]); db.flush()
            store = Store(business_id=business.id, name="Gupta Kirana - Main", city="Delhi", state="Delhi", pin_code="110092")
            member = BusinessUser(business_id=business.id, user_id=owner.id, role=Role.OWNER)
            log = AuditLog(business_id=business.id, user_id=owner.id, action="seed", entity="business", record_id=business.id)
            db.add_all([store, member, log, Subscription(business_id=business.id, plan="smart", image_limit=100)]); db.flush()
        category_names=["Atta and flour","Rice","Pulses and dals","Edible oil and ghee","Salt sugar and spices","Biscuits","Namkeen and snacks","Packaged food","Tea and coffee","Beverages","Dairy","Personal care","Household cleaning","Laundry","Stationery"]
        categories=[Category(business_id=business.id,name=n,gst_rate=5 if i<12 else 18) for i,n in enumerate(category_names)]; db.add_all(categories); db.flush()
        suppliers=[Supplier(business_id=business.id,name=f"Demo Distributor {i+1}",mobile=f"98{70000000+i:08d}",lead_time_days=1+i%5) for i in range(10)]; db.add_all(suppliers)
        customers=[Customer(business_id=business.id,name=f"Demo Customer {i+1}",mobile=f"97{60000000+i:08d}",credit_limit=5000) for i in range(30)]; db.add_all(customers); db.flush()
        products=[]
        stems=["Premium Atta","Basmati Rice","Toor Dal","Sunflower Oil","Iodised Salt","Glucose Biscuit","Masala Namkeen","Instant Noodles","Assam Tea","Mango Drink","Toned Milk","Bath Soap","Floor Cleaner","Washing Powder","Notebook"]
        for i in range(100):
            c=categories[i%len(categories)]; name=f"{stems[i%len(stems)]} {1+(i%5)} {'kg' if i%3==0 else 'pack'}"; cost=20+(i%25)*7
            p=Product(business_id=business.id,store_id=store.id,category_id=c.id,preferred_supplier_id=suppliers[i%10].id,code=f"KS-{i+1:04d}",name=name,base_unit="packet",purchase_unit="box",selling_unit="packet",conversion_factor=12,mrp=cost*1.25,selling_price=cost*1.18,purchase_cost=cost,reorder_level=8+(i%6)); products.append(p)
        db.add_all(products); db.flush()
        for i,p in enumerate(products): db.add(InventoryMovement(business_id=business.id,store_id=store.id,product_id=p.id,movement_type="opening_stock",quantity_base=float(i%19),unit_cost=p.purchase_cost,created_by=owner.id))
        for i,c in enumerate(customers[:10]): db.add(LedgerEntry(business_id=business.id,party_type="customer",party_id=c.id,entry_type="opening_balance",amount=250+i*175,created_by=owner.id))
        for i,cat in enumerate(["Rent","Electricity","Transportation","Packaging","Internet"]): db.add(Expense(business_id=business.id,store_id=store.id,category=cat,amount=500+i*225,payment_method="cash",payee="Demo Payee",created_by=owner.id))
        db.commit(); print("Seeded demo owner, 100 products, 15 categories, 10 suppliers and 30 customers")

if __name__ == "__main__": run()

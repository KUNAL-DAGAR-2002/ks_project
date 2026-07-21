import { index, integer, real, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

const audit = {
  createdAt: text("created_at").notNull().default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").notNull().default("CURRENT_TIMESTAMP"),
};

export const businesses = sqliteTable("businesses", {
  id: text("id").primaryKey(), name: text("name").notNull(), ownerName: text("owner_name").notNull(),
  currency: text("currency").notNull().default("INR"), language: text("language").notNull().default("en"), ...audit,
});
export const businessUsers = sqliteTable("business_users", {
  id: text("id").primaryKey(), tenantId: text("tenant_id").notNull().references(() => businesses.id),
  email: text("email").notNull(), role: text("role").notNull().default("owner"), active: integer("active", { mode:"boolean" }).notNull().default(true), ...audit,
}, t => [uniqueIndex("business_user_tenant_email_uq").on(t.tenantId,t.email), index("business_user_tenant_idx").on(t.tenantId)]);
export const stores = sqliteTable("stores", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), name:text("name").notNull(), city:text("city"), state:text("state"), ...audit,
}, t => [index("stores_tenant_idx").on(t.tenantId)]);
export const categories = sqliteTable("categories", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), name:text("name").notNull(), gstRate:real("gst_rate").notNull().default(0), active:integer("active",{mode:"boolean"}).notNull().default(true), ...audit,
}, t => [uniqueIndex("categories_tenant_name_uq").on(t.tenantId,t.name)]);
export const products = sqliteTable("products", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), storeId:text("store_id").notNull().references(() => stores.id),
  code:text("code").notNull(), name:text("name").notNull(), categoryId:text("category_id").references(() => categories.id), barcode:text("barcode"), unit:text("unit").notNull().default("piece"),
  mrp:real("mrp").notNull().default(0), sellingPrice:real("selling_price").notNull().default(0), purchaseCost:real("purchase_cost").notNull().default(0), reorderLevel:real("reorder_level").notNull().default(0), safetyStock:real("safety_stock").notNull().default(0), active:integer("active",{mode:"boolean"}).notNull().default(true), ...audit,
}, t => [uniqueIndex("products_tenant_code_uq").on(t.tenantId,t.code), uniqueIndex("products_tenant_barcode_uq").on(t.tenantId,t.barcode), index("products_tenant_store_idx").on(t.tenantId,t.storeId), index("products_category_idx").on(t.categoryId)]);
export const suppliers = sqliteTable("suppliers", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), name:text("name").notNull(), mobile:text("mobile"), gstin:text("gstin"), leadTimeDays:integer("lead_time_days").notNull().default(2), outstanding:real("outstanding").notNull().default(0), active:integer("active",{mode:"boolean"}).notNull().default(true), ...audit,
}, t => [index("suppliers_tenant_idx").on(t.tenantId)]);
export const customers = sqliteTable("customers", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), name:text("name").notNull(), mobile:text("mobile"), openingBalance:real("opening_balance").notNull().default(0), creditLimit:real("credit_limit").notNull().default(0), ...audit,
}, t => [index("customers_tenant_idx").on(t.tenantId)]);
export const salesInvoices = sqliteTable("sales_invoices", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), storeId:text("store_id").notNull().references(() => stores.id), invoiceNumber:text("invoice_number").notNull(), customerId:text("customer_id").references(() => customers.id), transactionDate:text("transaction_date").notNull(), gross:real("gross").notNull(), discount:real("discount").notNull().default(0), net:real("net").notNull(), paymentMode:text("payment_mode").notNull(), status:text("status").notNull().default("posted"), ...audit,
}, t => [uniqueIndex("sales_tenant_invoice_uq").on(t.tenantId,t.invoiceNumber), index("sales_tenant_date_idx").on(t.tenantId,t.transactionDate)]);
export const salesLines = sqliteTable("sales_lines", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull(), invoiceId:text("invoice_id").notNull().references(() => salesInvoices.id), productId:text("product_id").notNull().references(() => products.id), quantity:real("quantity").notNull(), unitPrice:real("unit_price").notNull(), cost:real("cost").notNull(), net:real("net").notNull(),
}, t => [index("sales_lines_tenant_product_idx").on(t.tenantId,t.productId)]);
export const inventoryMovements = sqliteTable("inventory_movements", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), storeId:text("store_id").notNull().references(() => stores.id), productId:text("product_id").notNull().references(() => products.id), movementType:text("movement_type").notNull(), quantity:real("quantity").notNull(), unitCost:real("unit_cost").notNull().default(0), referenceType:text("reference_type"), referenceId:text("reference_id"), transactionDate:text("transaction_date").notNull(), createdBy:text("created_by").notNull(), createdAt:text("created_at").notNull().default("CURRENT_TIMESTAMP"),
}, t => [index("inventory_tenant_store_product_idx").on(t.tenantId,t.storeId,t.productId), index("inventory_tenant_date_idx").on(t.tenantId,t.transactionDate)]);
export const customerLedger = sqliteTable("customer_ledger", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), customerId:text("customer_id").notNull().references(() => customers.id), entryType:text("entry_type").notNull(), amount:real("amount").notNull(), dueDate:text("due_date"), referenceId:text("reference_id"), transactionDate:text("transaction_date").notNull(), createdBy:text("created_by").notNull(), ...audit,
}, t => [index("ledger_tenant_customer_date_idx").on(t.tenantId,t.customerId,t.transactionDate), index("ledger_due_idx").on(t.tenantId,t.dueDate)]);
export const expenses = sqliteTable("expenses", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), storeId:text("store_id").notNull().references(() => stores.id), transactionDate:text("transaction_date").notNull(), category:text("category").notNull(), amount:real("amount").notNull(), paymentMethod:text("payment_method").notNull(), notes:text("notes"), createdBy:text("created_by").notNull(), ...audit,
}, t => [index("expenses_tenant_date_idx").on(t.tenantId,t.transactionDate)]);
export const alerts = sqliteTable("alerts", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), storeId:text("store_id").notNull(), type:text("type").notNull(), severity:text("severity").notNull(), entityId:text("entity_id"), message:text("message").notNull(), status:text("status").notNull().default("open"), createdAt:text("created_at").notNull().default("CURRENT_TIMESTAMP"),
}, t => [index("alerts_tenant_status_idx").on(t.tenantId,t.status)]);
export const auditLogs = sqliteTable("audit_logs", {
  id:text("id").primaryKey(), tenantId:text("tenant_id").notNull().references(() => businesses.id), userEmail:text("user_email").notNull(), action:text("action").notNull(), entity:text("entity").notNull(), recordId:text("record_id").notNull(), previousValue:text("previous_value"), newValue:text("new_value"), createdAt:text("created_at").notNull().default("CURRENT_TIMESTAMP"),
}, t => [index("audit_tenant_created_idx").on(t.tenantId,t.createdAt)]);

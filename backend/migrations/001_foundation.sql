CREATE TABLE users (id UUID PRIMARY KEY, mobile VARCHAR(15) NOT NULL UNIQUE, name VARCHAR(120) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE businesses (id UUID PRIMARY KEY, name VARCHAR(160) NOT NULL, preferred_language VARCHAR(10) NOT NULL DEFAULT 'en', currency VARCHAR(3) NOT NULL DEFAULT 'INR', onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE business_users (id UUID PRIMARY KEY, business_id UUID NOT NULL REFERENCES businesses(id), user_id UUID NOT NULL REFERENCES users(id), role VARCHAR(30) NOT NULL CHECK (role IN ('owner','manager','staff','platform_admin')), active BOOLEAN NOT NULL DEFAULT TRUE, UNIQUE (business_id,user_id));
CREATE TABLE stores (id UUID PRIMARY KEY, business_id UUID NOT NULL REFERENCES businesses(id), name VARCHAR(160) NOT NULL, city VARCHAR(80) NOT NULL, state VARCHAR(80) NOT NULL, pin_code VARCHAR(6) NOT NULL CHECK (pin_code ~ '^[1-9][0-9]{5}$'));
CREATE TABLE audit_logs (id UUID PRIMARY KEY, business_id UUID NOT NULL REFERENCES businesses(id), user_id UUID NOT NULL REFERENCES users(id), action VARCHAR(80) NOT NULL, entity VARCHAR(80) NOT NULL, record_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX business_users_business_idx ON business_users(business_id);
CREATE INDEX business_users_user_idx ON business_users(user_id);
CREATE INDEX stores_business_idx ON stores(business_id);
CREATE INDEX audit_logs_business_created_idx ON audit_logs(business_id,created_at);

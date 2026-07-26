# KiranaSaathi — Milestone 1

## Product understanding

KiranaSaathi is a mobile-first, multi-tenant operating system for an owner-operated Indian kirana. It will accept manual, fixed-template and image-extracted business data, but nothing extracted by AI may affect stock or money before explicit confirmation.

## Assumptions

- The first release supports one owner and one store, while membership tables allow staff and future multiple stores.
- Mobile OTP is represented by a provider-independent request/verify API; development uses `123456` and production must connect an SMS provider.
- PostgreSQL is authoritative in production. SQLite is the local test default.
- INR, Hinglish-ready copy and Indian PIN/mobile validation are defaults.
- Product, transaction, OCR and reporting work belongs to later milestones.

## MVP versus future

The MVP ends at Milestone 8 and includes all fixed kirana workflows in the brief. Later work includes multi-store transfers, arbitrary integrations, full GST/accounting, autonomous AI posting and advanced forecasting.

## Architecture

```text
Next.js mobile web app
        |
FastAPI REST API / OpenAPI
        |
Auth → tenant membership guard → module service → repository
        |                                  |
PostgreSQL                            background jobs (M4+)
        |
private object storage (M4+)
```

Every protected request resolves the authenticated user on the server. Business queries join through `business_users`; an unknown or unauthorized business returns 404 to avoid tenant enumeration.

## Foundation entity relationships

```text
User 1 ── * BusinessUser * ── 1 Business 1 ── * Store
                                  |
                                  └── * AuditLog
```

Future tables will always carry `business_id`, use compound tenant indexes and reject cross-tenant references in the service layer and database.

## Image architecture reserved for Milestone 4

`upload → private storage → quality check → OCR adapter → extraction adapter → strict schema validation → product matching → human review → confirmed transactional posting`

The strict schemas will be versioned per document type: sales page, purchase invoice, stock count, udhaar, expense and product list. Raw OCR, confidence, corrections and confirmation identity will remain immutable audit evidence. No image implementation is included in Milestone 1.

## Project structure

```text
app/                       Next.js user interface
backend/app/main.py        API routes
backend/app/models.py      foundation domain model
backend/app/security.py    JWT authentication dependency
backend/app/schemas.py     request/response validation
backend/migrations/        PostgreSQL migrations
backend/tests/             integration and isolation tests
backend/seed.py            fictional demo tenant
db/                        hosted Sites schema
docs/                      milestone decisions
```

## Milestone 1 API

- `GET /api/health`
- `POST /api/auth/otp/request`
- `POST /api/auth/otp/verify`
- `POST /api/onboarding`
- `GET /api/businesses`
- `GET /api/businesses/{business_id}`
- OpenAPI: `/docs`

## Frontend route plan

- `/login`, `/onboarding`, `/app`, `/app/settings` — Milestone 1
- `/app/products`, `/app/categories`, `/app/suppliers`, `/app/customers`, `/app/opening-stock` — Milestone 2
- Transaction, image, import, analytics and reporting routes follow their named milestones.

## Inventory posting rule

From Milestone 3 onward, stock is the sum of immutable movements; current stock is never overwritten. Posting is transactional and idempotent. Reversals create opposite movements. AI and imports post only after confirmation.

## Milestone map

1. Foundation
2. Kirana masters
3. Core transactions
4. Image intelligence
5. Fixed imports
6. Analytics and alerts
7. Reports and SaaS operations
8. Quality and deployment

## Run and verify

```powershell
& "C:\Users\Kunal\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
cd backend
python seed.py
python -m uvicorn app.main:app --reload --port 8000
python -m pytest -q
```

If PowerShell blocks activation, skip `Activate.ps1` and run `.\.venv\Scripts\python.exe` explicitly from the project root.

Demo mobile: `9876543210`. Development OTP: `123456`.

## Manual review checklist

- Invalid Indian mobile numbers are rejected.
- Incorrect OTP is rejected.
- Valid OTP returns a bearer token.
- Onboarding creates exactly one business, store, owner membership and audit log.
- A second onboarding attempt returns conflict.
- Owner A cannot retrieve Owner B's business.
- `/docs` describes the five foundation workflows.

## Known limitations

- OTP delivery is a development adapter, not an SMS integration.
- The production PostgreSQL migration is supplied but automated Alembic revision management begins before Milestone 2.
- The existing dashboard preview predates this milestone and is not connected to the new API; it is not counted as Milestone 1 acceptance.
- Product masters and all transaction data intentionally remain unimplemented until review approval.

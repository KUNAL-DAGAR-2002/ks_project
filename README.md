# KiranaSaathi

Mobile-first kirana operations MVP: OTP onboarding, tenant isolation, masters, sales, purchases, stock ledger, udhaar/payables, expenses, dashboard calculations, reorder planning, CSV imports/exports, Gemini-powered image extraction with mandatory confirmation, audit logging and subscription usage foundations.

## Start on Windows

1. Double-click `start-backend.cmd`.
2. Double-click `start-frontend.cmd`.
3. Open `http://localhost:3000`; API docs are at `http://localhost:8000/docs`.

Demo mobile: `9876543210`; development OTP: `123456`.

## Gemini configuration

Never commit the API key. Before starting the backend in PowerShell:

```powershell
$env:KIRANA_GEMINI_API_KEY="your-testing-key"
```

The image flow is `upload → Gemini extraction → review_required → human confirmation`. Reviewed rows are posted through the normal purchase, stock or ledger endpoints.

## Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
cd ..
npm.cmd test
```

## Docker

```powershell
$env:KIRANA_JWT_SECRET="a-long-random-secret"
$env:KIRANA_GEMINI_API_KEY="your-testing-key"
docker compose up --build
```

## Implemented API groups

- OTP authentication, onboarding, tenant membership and roles
- Categories, products, aliases, suppliers and customers
- Opening stock and immutable inventory movements
- Sales, purchases, customer/supplier ledger payments and expenses
- Database-derived dashboard, current inventory and reorder list
- Fixed product CSV validation/import and daily CSV export
- Private tenant upload paths, Gemini vision extraction and confirmation audit
- Subscription record, image allowance fields and audit history foundation

## Current production gaps

This repository is a broad end-to-end MVP, not a claim of enterprise completeness. Before public production: connect an SMS OTP provider; move uploads to private S3/Supabase storage; replace development `create_all` with reviewed Alembic migrations; add malware scanning and background workers; expand browser E2E coverage; add PDF/Excel report endpoints; enforce image usage counters; and deploy the FastAPI/PostgreSQL services separately from the Sites frontend.

## Render deployment

The repository includes `render.yaml` for a Render Blueprint with the FastAPI
backend and Vinext frontend. Connect an existing Render PostgreSQL database by
supplying its URL as `KIRANA_DATABASE_URL`.

Before the first deploy, provide these secret values in Render:

- `KIRANA_GEMINI_API_KEY`
- `KIRANA_DATABASE_URL`
- `KIRANA_ADMIN_USERNAME`
- `KIRANA_ADMIN_PASSWORD`
- `KIRANA_CORS_ORIGINS` — the deployed frontend origin, for example
  `https://kirana-saathi.onrender.com`
- `VITE_API_URL` — the deployed backend API URL, for example
  `https://kirana-saathi-api.onrender.com/api`

The backend accepts Render's standard `postgresql://` connection URL and
automatically selects the installed Psycopg 3 driver. Both web services bind to
Render's supplied `PORT`.

Render filesystems are ephemeral. For durable uploaded source images, attach a
persistent disk to the backend at `/opt/render/project/src/uploads`. Confirmed
business records remain in Render PostgreSQL regardless.

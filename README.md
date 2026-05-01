# Backend README

## Overview

This backend is a FastAPI service for a village-based collection and lending workflow. It handles authentication, user registration, dashboard reporting, village and customer management, installment generation, payment collection, delayed-customer tracking, and collection-history reporting.

The backend uses MongoDB as the primary datastore and exposes all application endpoints under the `/api` prefix, with a separate `/health` endpoint for service checks.

For deployment compatibility, the same application routes are also available without the top-level `/api` prefix when a reverse proxy or hosting platform strips that prefix before forwarding the request.

## Tech Stack

- FastAPI `0.116.1`
- Uvicorn `0.35.0`
- PyMongo `4.13.2`
- `python-jose[cryptography]` for JWT tokens
- `pwdlib[argon2]` for password hashing
- `pydantic-settings` for environment-based configuration
- `python-dotenv` for loading `.env`

## Core Responsibilities

### 1. Authentication and Authorization

- Login using email or phone number plus password
- Register new users
- Issue bearer access tokens
- Store role information in the authenticated user payload
- Support admin and customer roles
- Support linked collaborator/customer accounts through `linked_admin_id`

### 2. Dashboard Reporting

- Return authenticated dashboard payloads
- Aggregate day-wise invested amount and returns
- Compute profit/loss summary cards
- Track delayed customers
- Compute overdue amount totals
- Return top delayed customers with collector context

### 3. Village Management

- Create villages
- List villages
- Update villages
- Delete villages
- Include customer counts per village
- Track villages by collection day

### 4. Customer Management

- Create customers inside a village
- Update customers
- Delete customers
- Return enriched customer data for lists and detail views
- Store customer identity and loan information
- Keep customer-level overdue and collection metrics available to the frontend

### 5. Installment Generation and Tracking

- Automatically create installments for each customer based on payment plan
- Support these payment plans:
  - daily
  - weekly
  - monthly
  - yearly
- Track installment fields including:
  - due date
  - amount due
  - amount paid
  - remaining balance
  - status
- Mark installments as `pending`, `partial`, `paid`, or `skipped`

### 6. Collection Recording

- Record collection against a selected installment
- Allow payment modes:
  - `phonepe`
  - `gpay`
  - `cash`
- Capture collector identity
- Capture collection timestamp
- Capture optional note
- Allocate payment oldest-first across all unpaid installments up to the selected due date
- Support carry-forward balances when older dues remain unpaid
- Reject over-collection beyond the cumulative remaining amount up to the selected installment

### 7. Collection History and Event Grouping

- Store payment records in a dedicated `collections` collection
- Group multiple installment allocations from a single visit into one logical batch
- Store:
  - collection batch ID
  - anchor installment ID
  - amount paid
  - covered installment IDs
  - covered installment count
  - payment mode
  - collector name and user ID
  - collection time
- Return grouped collection history to the frontend

### 8. Customer Detail Calendar Semantics

- Build a customer installment calendar for detail view
- Return overdue flags
- Return remaining cumulative amount for selected due dates
- Return last collector and payment mode metadata for each row
- Preserve anchor payment event details for the actual payment-date row
- Support UI display of grouped catch-up payments without incorrectly marking old rows as newly paid on later dates

### 9. Collaborator Management

- List collaborators linked to an admin account
- Toggle whether an admin allows new collaborators to link to them

## API Surface

### Health

- `GET /health`

### Auth

- `POST /api/auth/login`
- `GET /api/auth/register/admins` to fetch available admin accounts for registration linking
- `POST /api/auth/register`

### Dashboard

- `GET /api/auth/dashboard`

### Profile

- `GET /api/auth/profile/collaborators`
- `PATCH /api/auth/profile/collaborator-settings`

### Finance

- `GET /api/finance/villages`
- `POST /api/finance/villages`
- `PUT /api/finance/villages/{village_id}`
- `DELETE /api/finance/villages/{village_id}`
- `GET /api/finance/villages/{village_id}/customers`
- `POST /api/finance/villages/{village_id}/customers`
- `GET /api/finance/customers/{customer_id}`
- `PUT /api/finance/customers/{customer_id}`
- `DELETE /api/finance/customers/{customer_id}`
- `POST /api/finance/installments/{installment_id}/collect`

## Project Structure

```text
backend/
  requirements.txt
  app/
    main.py
    api/
      auth.py
      dashboard.py
      deps.py
      finance.py
      login.py
      profile.py
      register.py
      router.py
    core/
      config.py
      security.py
    db/
      mongodb.py
    models/
      finance.py
      user.py
  scripts/
    seed_demo_users.py
```

## Data Model Overview

The backend operates primarily with these MongoDB collections:

- `users`
- `villages`
- `customers`
- `installments`
- `collections`

### Collection Purposes

- `users`: authentication, profile, role, collaborator permissions
- `villages`: village name and assigned collection day
- `customers`: customer profile and loan metadata
- `installments`: generated payment schedule and running payment state
- `collections`: payment event log with collector, mode, batch, and history details

## Configuration

Environment variables are loaded from `backend/.env`.

Required settings:

- `MONGODB_URL`
- `MONGODB_DB`
- `JWT_SECRET_KEY`

Optional settings with defaults:

- `JWT_ALGORITHM=HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES=120`
- `API_PORT=8010`
- `CORS_ORIGINS=http://localhost:8081,http://localhost:19006,http://localhost:3000`

### Example MongoDB URL

If your MongoDB requires authentication, use a full connection string such as:

```text
mongodb://username:password@localhost:27017/app_development?authSource=admin
```

## Local Setup

### Install dependencies

```bash
pip install -r backend/requirements.txt
```

### Seed demo users

```bash
python backend/scripts/seed_demo_users.py
```

### Run the API from workspace root

```bash
python -m uvicorn --app-dir backend app.main:app --reload
```

### Alternative run from backend folder

```bash
python app/main.py
```

The application title is:

```text
App Development API
```

## Verification Commands

### Backend import smoke test

```bash
python -c "from app.main import app; print(app.title)"
```

Expected output:

```text
App Development API
```

### Health check

```text
GET http://localhost:8010/health
```

Expected response:

```json
{"status":"ok"}
```

## Demo Accounts

When the seed script is used, these accounts are expected to exist:

- `admin@example.com` / `Password@123`
- `manager@example.com` / `Password@123`
- `user@example.com` / `Password@123`

The same password also works for the seeded phone numbers referenced in the workspace root README.

## Business Rules Already Implemented

- Customer dues can roll forward into a later selected installment
- Payment allocation happens against the oldest unpaid dues first
- Grouped catch-up payments are represented as one visit in history
- Collector identity and payment mode are persisted for each payment event
- Delayed customers are surfaced in dashboard reporting
- Customer detail responses include both operational metrics and collection history

## Operational Notes

- The frontend expects the backend on port `8010` unless overridden by environment settings on the client side.
- Local MongoDB connectivity is not enough by itself if authentication is enabled; a valid credentialed `MONGODB_URL` is required.
- CORS origins are controlled through the `CORS_ORIGINS` setting and support multiple comma-separated values.
- The finance API is the main business-logic layer for villages, customers, installments, and collections.

## Current Product Scope

This backend currently supports a full lending and field-collection workflow including:

- auth and registration
- admin-linked collaborator accounts
- dashboard analytics
- delayed customer monitoring
- village scheduling
- customer record management
- installment generation
- collection recording with payment modes
- collector attribution
- grouped collection history
# FixMyArea

**Report it. Track it. Improve your community.**

An early pilot MVP for reporting public infrastructure issues, tracking organization action and publishing evidence of resolution. This is live workflow software; it does not seed fabricated public statistics or reports.

## What works

Citizen registration and sign in; photo and public issue location; report ID; public map; proximity and category duplicate suggestions; support for existing reports; personal tracking and in-app notices; organization assignment; controlled status progression; after photos; public timeline; basic impact counts. FastAPI documents the API at `/docs`.

## Run locally

Python 3.12 and Node 20+ are recommended.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export JWT_SECRET=local-secret-change-me
uvicorn app:app --reload
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. The local API uses SQLite by default. On Render, `DATABASE_URL` uses managed PostgreSQL. The frontend API host is provided by the Blueprint. Uploaded photos are JPEG compressed and stored in the database for this pilot; migrate them to S3-compatible private object storage before scaling.

## Deploy

`render.yaml` provisions a free API and static frontend. The database is an external PostgreSQL service, such as a separate Neon Free project. Create the database, obtain its pooled connection string with TLS enabled, and enter it as the API's `DATABASE_URL` secret in Render. Do not put the connection string in GitHub or chat. The API refuses to start on Render if the secret is absent, preventing use of ephemeral SQLite storage. Open [the Render Blueprint setup](https://dashboard.render.com/blueprint/new?repo=https://github.com/kwaw-ebn/FIXMYAREA), review and apply. Check `/health`, `/docs`, the frontend, and the report workflow after deployment. Ensure your custom frontend domain is added to `FRONTEND_ORIGINS` if you add one. Neon Free has resource limits; monitor storage because pilot photos are currently compressed into the database.

## Organization onboarding

Public signup grants the `citizen` role only. No administrator account is seeded. A trusted operator can provision an organization and its first organization administrator with `backend/bootstrap.py` using a secure database connection. The script rejects existing email addresses and hashes the password. Never run it with untrusted inputs or publish credentials. Once created, organization staff accounts currently require operator provisioning; a self-service invitation workflow is future work. Platform administrators can be provisioned by a trusted operator with an explicit database procedure; this MVP does not expose a public elevation route.

## Security and pilot limits

JWTs expire after eight hours. Passwords use Argon2. Organization report reads and mutations are scoped server-side. Images are decoded, resized and capped at 6 MB. Public issue locations and evidence are visible; contributors should avoid faces, homes, license plates and personal details. A privacy/moderation review and anti-spam controls are needed before a broad public launch. Registration does not yet verify email. There is no SMS, WhatsApp, offline field sync or AI model in this MVP. The public map uses OpenStreetMap tile service; arrange suitable tile hosting for significant traffic.

## Workflow

Submitted → Under Review → Verified → Assigned → In Progress → Resolved → Closed. Assignment is restricted to the assigned organization or platform administrator. Resolution requires an after image. Each transition creates an event and a notice. Duplicate suggestions use same category, 250 m proximity and a 90-day window; they are advisory.

## Roadmap

Next: field verification and rejection/reopening, proper invitations and email verification, moderation, durable object storage, rate limiting, maps provider, location jurisdiction routing, full audit export and tests. Later: AI classification, offline capture, messaging integrations, GIS hotspots, SLA automation and partner workspace. Never claim a municipality has joined until onboarded.


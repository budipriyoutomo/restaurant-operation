# RestaurantOps — Production Deployment

Backend + PostgreSQL run on a VPS via Docker Compose. Frontend deploys to
Vercel or Netlify and talks to the backend over HTTPS.

```
  Vercel / Netlify  ──HTTPS──►  Caddy (TLS)  ──►  api (FastAPI/Gunicorn)  ──►  db (Postgres 17)
   Next.js frontend               :443              :8000 (localhost only)        volume: pgdata
                                                     uploads volume
                                              pm-generator (hourly PM job)
```

---

## 1. Backend on the VPS

### Prerequisites
- A VPS (Ubuntu 22.04+ recommended), Docker Engine + Compose plugin installed.
- A DNS `A` record for the API, e.g. `api.yourdomain.com` → VPS IP (needed only
  if you use the bundled Caddy TLS proxy).
- Ports 80/443 open (Caddy) or 8000 fronted by your own proxy.

### Files that ship
Everything is under `backend/`:

| File | Purpose |
|---|---|
| `Dockerfile` | API image (Python 3.14, Gunicorn + Uvicorn workers) |
| `docker-compose.yml` | `db`, `api`, `pm-generator`, optional `caddy` |
| `gunicorn.conf.py` | worker/timeout/proxy config, env-overridable |
| `docker/entrypoint.sh` | waits for DB → `alembic upgrade head` → (optional) seed → start |
| `.env.production.example` | template for `.env.production` (never commit the real one) |
| `Caddyfile` | optional automatic-HTTPS reverse proxy |
| `scripts/set_password.py` | rotate a user's password from the shell (break-glass when no admin can sign in; in the app: Settings → Security, Users → Reset password) |

### Steps

```bash
# 1. Copy the backend/ folder to the VPS (git clone, scp, rsync…)
cd backend

# 2. Create the real env file and fill EVERY value
cp .env.production.example .env.production
python3 -c "import secrets; print(secrets.token_hex(32))"   # -> SECRET_KEY
#   set POSTGRES_PASSWORD, SECRET_KEY, CORS_ORIGINS (your Vercel/Netlify domain)
#   for the first boot only: RUN_SEED=1

# 3. Build and start (without TLS proxy — API on 127.0.0.1:8000)
docker compose --env-file .env.production up -d --build

# 3b. …or WITH the bundled TLS proxy (set DOMAIN + ACME_EMAIL in .env.production first)
docker compose --env-file .env.production --profile proxy up -d --build

# 4. Watch it come up
docker compose --env-file .env.production logs -f api
curl -fsS http://127.0.0.1:8000/health      # {"status":"ok","environment":"production"}

# 5. After the first successful boot: set RUN_SEED=0 in .env.production, then
docker compose --env-file .env.production up -d
```

> Tip: `alias dc='docker compose --env-file .env.production'` to shorten the commands below.

### First admin user

If you set `RUN_SEED=1` on the first boot, `scripts/seed.py` creates
`admin@restaurant.com` / `manager@restaurant.com` / `staff@restaurant.com` with
**default demo passwords** (`admin123`, …). **Rotate them immediately:**

```bash
dc exec api python -m scripts.set_password admin@restaurant.com
# prints a generated strong password once; repeat for the others or delete them
```

Prefer not to seed demo users at all? Keep `RUN_SEED=0` and onboard every
customer through the platform admin (next section).

### Companies and the platform admin (multi-tenancy, Todo-Pilot §11)

Every business row belongs to one **company**; users see only their own
company's data (enforced by the ORM — `app/core/tenancy.py`). Migration 040
moves all existing data into **"Default Company"** (slug `default`).

The SaaS operator is a **platform admin**: an account with no company that
manages companies from the Platform page and cannot read any company's data.
Create the first one once:

```bash
dc exec api python -m scripts.create_platform_admin ops@yourdomain.com "Ops Team"
# prints a generated password once
```

Sign in with it on the frontend → **Platform** page:

- rename "Default Company" to the pilot customer's real name;
- **New company** creates a customer in one step: the company, its default
  roles (admin/manager/staff), an optional first outlet and its first admin
  (password generated and shown once — send it to them);
- **Deactivate** signs all of that company's users out and blocks sign-in;
  nothing is deleted, and **Activate** restores access.

Email addresses are unique across the whole platform: one account belongs to
exactly one company. Scheduled jobs (PM generator, escalation, WhatsApp retry)
run for every active company automatically.

**Upgrading an existing install to 040:** take a backup first (see Backups),
then `dc up -d --build` as usual. Existing users, roles and data end up in
"Default Company" and keep working unchanged; then create a platform admin.

### Preventive-maintenance job
`pm-generator` runs `scripts/run_pm_generator` every hour. It is idempotent and
only creates work orders that are actually due, so hourly is safe. Check it with
`dc logs pm-generator`.

### Approval escalation job
`approval-escalator` runs `scripts/run_escalate_stale` every hour. Pending
approvals whose active step has waited longer than 3 days
(`ESCALATION_THRESHOLD_DAYS` in `app/services/approval_service.py`) are flagged `escalated` and admins get a critical notification. It is idempotent:
a request is escalated once per stuck step. Check it with
`dc logs approval-escalator`.

Without Docker, use the systemd units in `deploy/`
(`restaurantops-escalate.{service,timer}`, same setup as the PM timer).

### Daily ops checklist job
`ops-checklist-scheduler` runs `scripts/run_ops_checklists` every 15 minutes.
For every active schedule due today it creates today's run — "today" and the
due time are the outlet's local time (`Outlets → Timezone`, default
`Asia/Jakarta`; Bali = `Asia/Makassar`, Malaysia = `Asia/Kuala_Lumpur`) — and
it locks runs whose window closed without a submit (`missed`). Idempotent: one
run per schedule per shift date is a database constraint. Check it with
`dc logs ops-checklist-scheduler`; for a demo, `Daily Ops → Jalankan sekarang`
(`POST /api/ops-checklists/run-now`, ops:manage) does the same for one company.

Without Docker, use `deploy/restaurantops-ops.{service,timer}` (every 15 minutes).

Weekly and monthly schedules (deep cleaning, thermometer calibration) go
through the same job: a run that opens days before its date is created once its
window opens.

To seed a pilot company with a built-in template set from the shell (the same
sets are one click in onboarding):

```bash
dc exec backend python -m scripts.seed_ops_templates <company_slug> resto --outlet "Dago"
```

### IoT temperature sensors
A manager adds a sensor in `CMMS → asset → Suhu → Sensor IoT` and gets its key
once. The device posts to `POST /api/temperature-sensors/readings` with the
header `X-Sensor-Key: rsk_…` and a body
`{"readings": [{"value": -18.2, "recordedAt": "2026-10-16T03:00:00Z"}]}`
(up to 500 per call, at most 24 hours old; retrying the same reading is
ignored). There is no user behind the request: the key identifies the company.
Escalation waits until an excursion has lasted `TEMP_SENSOR_SUSTAIN_MINUTES`
(default 15) and repeats at most every `TEMP_SENSOR_REESCALATE_MINUTES`
(default 60). The default rate limit (`RATE_LIMIT_DEFAULT`, per IP) also applies
to sensors: send readings in batches when many devices share one connection.

### Outlet digest job
`outlet-digest` runs `scripts/run_outlet_digest` every hour. Users who turned on
the daily and/or weekly summary (Settings → Notifications) and can open the
dashboard get, from 07:00 local time (`DIGEST_TZ`, default `Asia/Jakarta`), one
summary per day (weekly: Mondays) of their outlets — open issues, SLA, QA,
guests waiting, budget, downtime, exceptions first. In-app always; email and
WhatsApp per the user's channel settings. Check it with `dc logs outlet-digest`.

### Email notifications (optional)
Notifications are always stored in-app. To also send email, set SMTP in
`.env.production` and restart `api`, `pm-generator` and `approval-escalator`:

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_STARTTLS=true
SMTP_FROM=RestaurantOps <no-reply@yourdomain.com>
APP_URL=https://app.yourdomain.com     # link in the email body
```

Emailed events: approval waiting for a decision, approval escalated, request
approved/rejected (to the requester), work order assigned. Each user can switch
each one off under **Settings → Email**. Mail is sent after the database commit
on a background thread, so an SMTP outage never fails a user's action; failures
are logged (`dc logs api`). Leave `SMTP_HOST` empty to stay in-app only, or set
`EMAIL_BACKEND=console` to log emails instead of sending them.

### Forgot password

"Forgot password?" on the sign-in page emails a one-time link to
`APP_URL/reset-password?token=…`. It needs **both** `SMTP_HOST` and `APP_URL`;
without them the request is accepted but nothing is sent (a warning is logged),
and users must ask an admin (Users → Reset password). The link is valid for
`PASSWORD_RESET_EXPIRE_MINUTES` (default 60), works once, and at most
`PASSWORD_RESET_MAX_PER_HOUR` (default 3) links are sent per user per hour.
The endpoint answers the same for unknown emails, so it does not reveal who
has an account. Requires migration 053 (applied automatically on boot).

Every password change — Settings → Security, Users → Reset password, the
forgot-password link or `scripts/set_password.py` — **signs the user out of
every other session** (`users.token_version`, migration 054). Changing your own
password keeps the device you did it on signed in.

### Rate limits on sign-in

Per client IP, configurable in `.env.production`: `RATE_LIMIT_LOGIN`
(default `10/minute`), `RATE_LIMIT_FORGOT_PASSWORD` (`5/minute`),
`RATE_LIMIT_RESET_PASSWORD` (`10/minute`); over the limit the API answers 429
with `Retry-After`. The real client IP comes from the proxy's
`X-Forwarded-For` (`FORWARDED_ALLOW_IPS`), so keep the API port bound to
127.0.0.1 behind Caddy/Nginx. Counters are per gunicorn worker (in memory), so
the effective ceiling is limit × `WEB_CONCURRENCY`.

### WhatsApp notifications (optional)
WhatsApp goes out through a self-hosted [WuzAPI](https://github.com/asternic/wuzapi)
instance (one WhatsApp number paired by QR code in WuzAPI). Set in
`.env.production` and restart `api`, `pm-generator`, `approval-escalator` and
`whatsapp-retry`:

```bash
WUZAPI_URL=http://wuzapi:8080        # base URL reachable from the api container
WUZAPI_TOKEN=...                     # the WuzAPI user token (sent as the Token header)
WHATSAPP_MAX_PER_HOUR=20             # per recipient; extra messages are skipped
WHATSAPP_DEDUP_MINUTES=10            # same event + record + recipient = one message
APP_URL=https://app.yourdomain.com   # link at the bottom of each message
```

Events: approval waiting for my step, approval escalated (admins), my request
approved/rejected, work order assigned to me, issue ready to close (managers of
that outlet). WhatsApp is **opt-in**: each user saves their number and switches
it on under **Settings → WhatsApp** ("Send test" checks the pairing). Admins can
also set a number under Users & Roles (API: `PATCH /api/auth/users/{id}`).

Messages are written to the `whatsapp_outbox` table in the same transaction as
the action and sent after commit on a background thread, so WuzAPI being down
never fails a user's action. `whatsapp-retry` resends failures after 1, 5 and
30 minutes; after `WHATSAPP_MAX_ATTEMPTS` (4) the row is `failed`. Inspect with:

```bash
dc exec db psql -U restaurantops -c "SELECT status, count(*) FROM whatsapp_outbox GROUP BY 1"
dc logs whatsapp-retry
```

Set `WHATSAPP_BACKEND=console` to log messages instead of sending them.
Without Docker: `deploy/restaurantops-whatsapp.{service,timer}`.

### Reverse proxy / TLS

**Option A — bundled Caddy** (`--profile proxy`): set `DOMAIN` and `ACME_EMAIL`
in `.env.production`, point DNS at the VPS, done. Caddy gets and renews a
Let's Encrypt cert automatically and proxies `:443 → api:8000`.

**Option B — your own Nginx/Traefik/Cloudflare Tunnel**: leave the proxy profile
off. The API listens on `127.0.0.1:8000`. Forward to it and preserve
`X-Forwarded-For` / `X-Forwarded-Proto` (Gunicorn already trusts them —
`FORWARDED_ALLOW_IPS=*`; tighten to your proxy IP if it's not on the same host).

### Updating

```bash
git pull                       # or re-copy the folder
dc up -d --build               # entrypoint runs `alembic upgrade head` automatically
```

### Backups

The `db-backup` service takes a backup once a day at `BACKUP_HOUR` (default
03:00, `TZ` default `Asia/Jakarta`):

- `pg_dump` custom-format archive of the database
- tarball of the `uploads` volume (work-order photos)

Files land in `./backups/` on the host (`BACKUP_HOST_DIR` to change it):
`daily/` keeps the last `BACKUP_KEEP_DAILY` (7) runs, and Sunday's run is also
copied to `weekly/`, which keeps `BACKUP_KEEP_WEEKLY` (4). A failed run leaves no
partial file and retries 10 minutes later. Check it with `dc logs db-backup`.

Take a backup now (e.g. before an upgrade):

```bash
dc exec db-backup sh /usr/local/bin/backup_db.sh
```

**Copy backups off the VPS.** A backup on the same disk does not survive losing
the VPS. Sync `./backups` to object storage from the host, e.g. with
[rclone](https://rclone.org) configured for S3/B2/R2 (root crontab):

```bash
30 4 * * * rclone sync /path/to/backend/backups remote:restaurantops-backups
```

**Restore** (destructive — overwrites the database):

```bash
dc stop api pm-generator approval-escalator     # no writes during the restore
dc exec db-backup sh /usr/local/bin/restore_db.sh /backups/daily/<file>.dump --yes
dc start api pm-generator approval-escalator

# Photos
docker run --rm -v backend_uploads:/data -v $PWD/backups/daily:/in alpine \
  sh -c 'tar xzf /in/uploads-<stamp>.tar.gz -C /data'
```

`restore_db.sh` refuses to run without `--yes`, and restores in a single
transaction, so a broken dump leaves the database unchanged. Test a restore once
after the first deploy (restore into a scratch database by setting
`PGDATABASE=<scratch>` on the `exec`).

### Security checklist
- [ ] `SECRET_KEY` is a unique 64-hex value, `ENVIRONMENT=production`
- [ ] `POSTGRES_PASSWORD` is long and random; DB port is **not** published (`expose`, not `ports`)
- [ ] Default seed passwords rotated or demo users deleted
- [ ] `CORS_ORIGINS` lists only your real frontend origin(s), all `https://`
- [ ] TLS in front of the API (Caddy profile or your own proxy)
- [ ] `.env.production` is `chmod 600` and never committed
- [ ] Firewall: only 22/80/443 inbound
- [ ] Backups scheduled and test-restored once

---

## 2. Frontend on Vercel or Netlify

The frontend is a standard Next.js 16 app in `frontend/`. Only one env var
matters: `NEXT_PUBLIC_API_URL` (the public HTTPS URL of the backend).

### Before deploying
There are two lockfiles in `frontend/` (`package-lock.json` and
`pnpm-lock.yaml`). Keep **one** so the platform picks the right package manager —
`package-lock.json` is the current one; delete `pnpm-lock.yaml`.

### Vercel
1. Import the repo, set **Root Directory** = `frontend`.
2. Framework preset: Next.js (auto). Build command / output: defaults.
3. Environment Variables → add `NEXT_PUBLIC_API_URL = https://api.yourdomain.com`
   for Production (and Preview if you want previews to hit the API).
4. Deploy. Note the resulting domain.

### Netlify
1. New site from repo, **Base directory** = `frontend`.
2. Build command `next build`, publish handled by the Netlify Next.js runtime
   (install the “Next.js” plugin if prompted).
3. Site settings → Environment → `NEXT_PUBLIC_API_URL = https://api.yourdomain.com`.
4. Deploy.

### After the frontend is live
Add its origin to the backend's `CORS_ORIGINS` in `.env.production` and restart:

```bash
# .env.production
CORS_ORIGINS=https://your-app.vercel.app,https://www.yourdomain.com

dc up -d api
```

`NEXT_PUBLIC_*` values are baked in at build time — after changing
`NEXT_PUBLIC_API_URL` you must redeploy the frontend.

---

## 3. Smoke test end to end

```bash
curl -fsS https://api.yourdomain.com/health
# open the frontend, log in, create an issue, upload a work-order photo
```

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
| `scripts/set_password.py` | rotate a user's password (no API endpoint for this) |

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

Prefer not to seed demo users at all? Keep `RUN_SEED=0`, then create your admin
directly (the register endpoint is admin-only, so bootstrap via the script):

```bash
dc exec api python - <<'PY'
from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password
import uuid, secrets
db = SessionLocal()
pw = secrets.token_urlsafe(18)
db.add(User(id=uuid.uuid4(), email="you@company.com", name="Owner",
            password_hash=hash_password(pw), role="admin"))
db.commit()
print("admin password:", pw)
PY
```

### Preventive-maintenance job
`pm-generator` runs `scripts/run_pm_generator` every hour. It is idempotent and
only creates work orders that are actually due, so hourly is safe. Check it with
`dc logs pm-generator`.

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

### Backups (do this)

```bash
# Nightly dump (add to root crontab)
0 3 * * * cd /path/to/backend && docker compose --env-file .env.production exec -T db \
  pg_dump -U restaurantops restaurantops | gzip > /var/backups/restaurantops-$(date +\%F).sql.gz

# Restore
gunzip -c backup.sql.gz | docker compose --env-file .env.production exec -T db \
  psql -U restaurantops -d restaurantops
```

Also back up the `uploads` volume (work-order photos):
`docker run --rm -v backend_uploads:/data -v $PWD:/out alpine tar czf /out/uploads.tgz -C /data .`

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

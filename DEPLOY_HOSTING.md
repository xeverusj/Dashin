# Hosting Dashin (dashboards + scraper ingest)

This deploys the **web app** (dashboards + client portal + the ingest API that
receives scraper pushes). The **scrapers/enricher stay on client machines** —
they open a real browser and need a home IP, so they can't run on a server.

One container serves everything on a single URL:
- `https://your-app/…` → the Streamlit dashboards
- `https://your-app/api/leads/import` → where desktop scrapers push (token-auth)
- `https://your-app/health` → health check

## What you need
- A host that runs Docker with a **persistent volume** (for the SQLite DB).
- A domain (optional but recommended) — otherwise you get the platform's URL.

---

## Option A — Railway (easiest, ~$5/mo)

1. Push this repo to GitHub (private is fine).
2. On [railway.app](https://railway.app): **New Project → Deploy from GitHub repo** → pick the repo. Railway auto-detects the `Dockerfile`.
3. **Add a Volume**: in the service → *Variables/Volumes* → add a volume mounted at **`/data`**. (This keeps the database across restarts — without it your data resets on every deploy.)
4. **Set variables** (service → Variables):
   - `DB_PATH=/data/dashin.db`
   - `ANTHROPIC_API_KEY=` *(optional — only if you use in-app AI features)*
   - Railway sets `PORT` automatically; the app reads it.
5. Deploy. Open the generated URL → log in with `admin@dashin.com` / `admin123`.
6. **Immediately change the admin password** (Admin → Users), and generate a scraper token (Admin → Org Settings → Desktop Scraper Tokens).

## Option B — Any VPS (Hetzner / DigitalOcean, ~$4-6/mo)

```bash
# on the server, with Docker installed:
git clone <your-repo> dashin && cd dashin
docker build -t dashin .
docker run -d --name dashin \
  -p 80:8080 \
  -v /srv/dashin-data:/data \
  -e DB_PATH=/data/dashin.db \
  --restart unless-stopped \
  dashin
```
Point your domain's DNS A-record at the server IP. Put Caddy/nginx or Cloudflare in front for HTTPS (recommended).

## Option C — Fly.io / Render
Both build from the `Dockerfile` the same way. Add a persistent volume mounted at `/data` and set `DB_PATH=/data/dashin.db`. Render/Fly inject `PORT` automatically.

---

## Connecting a client's scraper to the hosted app
On the client machine, in the scraper's `.env`:
```
DASHIN_API_URL=https://your-app          # no trailing /api
DASHIN_API_TOKEN=dsh_...                  # from Admin → Org Settings → Scraper Tokens
```
Now when they run a scraper it saves the local CSV **and** pushes into their org's
inventory automatically. Verify the endpoint any time:
```
curl https://your-app/health        # → {"ok": true, "service": "dashin-ingest"}
```

## Bringing your existing data
The image ships **without** any database (data is never baked into the image).
On first boot the app creates a fresh DB with the default admin. To carry over
your current local data instead, copy your `data/system/dashin.db` onto the
volume at `/data/dashin.db` before/after first boot (e.g. `docker cp` on a VPS,
or Railway's volume shell), then restart.

## Notes
- The **Smart Scraper** page inside the hosted dashboard won't launch a browser
  (there's none on the server) — that's expected. Scraping happens on client
  machines and flows in via the ingest API.
- Change `admin123` immediately. Consider setting a strong DB backup routine
  (Super Admin → Backups writes to `/data/backups`).

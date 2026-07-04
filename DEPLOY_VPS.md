# Deploy Dashin on a VPS behind Cloudflare (stable, long-run setup)

End result: your dashboard live at `https://app.yourdomain.com`, the scraper
ingest at `https://app.yourdomain.com/api/leads/import`, automatic HTTPS, no open
inbound ports, nightly backups. Scrapers run on client machines and push in.

We use **Cloudflare Tunnel** — the VPS makes an *outbound* connection to
Cloudflare, so you never expose a port or your server IP. It's the most stable +
secure way to put a VPS behind Cloudflare.

---

## 1. Provision the VPS
- Pick **Hetzner** (CX22, ~€4/mo) or **DigitalOcean** ($6/mo). Choose
  **Ubuntu 24.04**. 2 GB RAM is plenty.
- You get an IP and root SSH access. (With Cloudflare Tunnel you won't even need
  the IP public, but you SSH in to set up.)

## 2. Install Docker (on the VPS, via SSH)
```bash
ssh root@YOUR_VPS_IP
curl -fsSL https://get.docker.com | sh
```

## 3. Get the code onto the VPS
**Option A — private GitHub repo (recommended, easy updates):**
```bash
apt-get install -y git
git clone https://github.com/YOU/dashin.git /srv/dashin
cd /srv/dashin
```
**Option B — upload from your PC (no GitHub):** from your Windows machine
(PowerShell), from the project folder:
```powershell
scp -r . root@YOUR_VPS_IP:/srv/dashin
```
(then `cd /srv/dashin` on the VPS)

## 4. Build & run the app (bound to localhost — no public port)
```bash
cd /srv/dashin
docker build -t dashin .
mkdir -p /srv/dashin-data
docker run -d --name dashin \
  --restart unless-stopped \
  -p 127.0.0.1:8080:8080 \
  -v /srv/dashin-data:/data \
  -e DB_PATH=/data/system/dashin.db \
  dashin
# check it's up:
curl -s http://127.0.0.1:8080/health      # → {"ok": true, ...}
```
Note `-p 127.0.0.1:8080:8080` — the app is reachable only from the box itself;
Cloudflare Tunnel bridges the public URL to it.

## 5. Connect your domain via Cloudflare Tunnel
1. In the **Cloudflare dashboard → Zero Trust → Networks → Tunnels → Create a
   tunnel** → *Cloudflared* → name it `dashin`.
2. Cloudflare shows an install command with a token. On the VPS, run the tunnel
   as a container (swap in YOUR token):
   ```bash
   docker run -d --name cloudflared --restart unless-stopped \
     --network host \
     cloudflare/cloudflared:latest tunnel --no-autoupdate run --token YOUR_TUNNEL_TOKEN
   ```
3. Back in the dashboard, add a **Public Hostname** for the tunnel:
   - Subdomain: `app`  ·  Domain: `yourdomain.com`
   - Service: `HTTP`  →  `localhost:8080`
   - Save.
4. Open **https://app.yourdomain.com** — the login page. HTTPS is automatic; no
   certs, no open ports.

## 6. First login (do this immediately)
- Log in `admin@dashin.com` / `admin123`.
- **Change the admin password** (Admin → Users).
- Generate a scraper token: **Admin → Org Settings → Desktop Scraper Tokens**.
- Give each client that token + the URL; they paste it into the desktop scraper.

## 7. Automated nightly backups
```bash
chmod +x /srv/dashin/deploy/backup.sh
# test it once:
/srv/dashin/deploy/backup.sh
# schedule daily at 03:15:
( crontab -l 2>/dev/null; echo "15 3 * * * /srv/dashin/deploy/backup.sh >> /var/log/dashin-backup.log 2>&1" ) | crontab -
```
Backups land in `/srv/dashin-backups/` (keeps 14). For true safety, configure
`rclone` to copy them offsite (Backblaze B2 is ~free) and uncomment the last line
in `deploy/backup.sh`.

## 8. Unattended security updates (keeps the box healthy hands-off)
```bash
apt-get install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades   # choose "Yes"
```

---

## Updating the app later
```bash
cd /srv/dashin
git pull                 # or re-scp the files
docker build -t dashin .
docker stop dashin && docker rm dashin
docker run -d --name dashin --restart unless-stopped \
  -p 127.0.0.1:8080:8080 -v /srv/dashin-data:/data \
  -e DB_PATH=/data/system/dashin.db dashin
```
Your data is safe — it lives in the `/srv/dashin-data` volume, not the image.
Schema migrations run automatically on start.

## Notes
- The hosted **Smart Scraper** page won't open a browser (there's none on the
  server) — expected. Scraping happens on client machines via the desktop app.
- Only outbound connections exist (the tunnel), so there's no public port to
  attack. You can even close SSH to the world and use Cloudflare's SSH access.
- Bringing existing data: copy your local `data/system/dashin.db` to
  `/srv/dashin-data/system/dashin.db` before first start (make the dir first).

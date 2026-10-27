# Deploy on Oracle Cloud (Always Free) — no laptop needed

Everything (Telegram bot, Admin dashboard, both APIs, the 15-minute scheduler) runs on one free
Oracle server, starts on boot and restarts itself if it crashes. The dashboard is reached through
a Cloudflare tunnel and your permanent link https://quiet-sun-08f8.parteeksharma2006.workers.dev.

> ⚠️ **Only ONE copy of the bot may run.** Before starting the server, close the "Telegram bot"
> window on your laptop (Telegram allows one bot connection at a time).

## 1. Create the account (you, ~10 min)

1. Sign up at **https://signup.cloud.oracle.com** (the card is only for identity checks; Always Free resources are not charged).
2. Pick a **home region** close to you (e.g. *India West (Mumbai)* or *India South (Hyderabad)*). It can't be changed later.

## 2. Create the server (you, ~5 min)

**Menu → Compute → Instances → Create instance**

| Setting | Choose |
|---|---|
| Image | **Canonical Ubuntu 24.04** |
| Shape | **Ampere → VM.Standard.A1.Flex**, 2 OCPU, **12 GB** memory (all "Always Free eligible") |
| Networking | keep the default (public subnet, assign a public IPv4) |
| SSH keys | **Generate a key pair** → **Save private key** (e.g. `oracle.key`) |

Click **Create** and wait until it is *Running*. Copy the **Public IP address**.

*If A1 says "out of capacity", try again later or pick another availability domain; or use
VM.Standard.E2.1.Micro (1 GB) — too small for the rec engine, so A1 is strongly preferred.*

No firewall changes are needed: nothing is exposed directly, the dashboard goes out through Cloudflare.

## 3. Copy your keys to the server (your laptop, PowerShell)

```powershell
cd "C:\Users\Parteek Sharma\Documents\college\Parvaaz\ecommerce recommendation engine"
scp -i C:\path\to\oracle.key .env ubuntu@<PUBLIC-IP>:~/store.env
```

(If SSH complains the key is "too open", right-click the key → Properties → Security → leave only your user.)

## 4. Install everything (one command on the server)

```powershell
ssh -i C:\path\to\oracle.key ubuntu@<PUBLIC-IP>
```

then on the server:

```bash
curl -fsSL https://raw.githubusercontent.com/Parteek-17/recommendation-engine/main/deploy/oracle/setup.sh | bash
```

It installs Python and cloudflared, clones the repo, generates the data, trains the models
(10–20 min the first time) and starts six services. At the end it prints a
`https://…trycloudflare.com` address.

*Repo private? Then run `git clone https://<your-github-token>@github.com/Parteek-17/recommendation-engine.git ~/store`
first and `bash ~/store/deploy/oracle/setup.sh`.*

## 5. Point your permanent link at the server

Cloudflare dashboard → your Worker **quiet-sun-08f8** → Settings → Variables → set **TUNNEL_URL** to
the address from step 4 → Save / Deploy. Open https://quiet-sun-08f8.parteeksharma2006.workers.dev
and sign in with the `ADMIN_PASSWORD` from your `.env`.

## Day-to-day

| Task | Command on the server |
|---|---|
| Are things running? | `systemctl list-units 'store-*'` |
| Bot / dashboard logs | `journalctl -u store-bot -f` · `journalctl -u store-admin -f` |
| Current tunnel address | `bash ~/store/deploy/oracle/tunnel_url.sh` |
| Update to the latest code | `bash ~/store/deploy/oracle/setup.sh` |
| Restart everything | `sudo systemctl restart 'store-*'` |

The tunnel address changes only when the server or the tunnel restarts: run `tunnel_url.sh` and
update TUNNEL_URL in the Worker. The workers.dev link you shared never changes.

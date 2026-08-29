# Server install

Two paths: Docker or systemd. Same result, Docker is less work.

> **Not on your gaming PC.** When the PC is off — exactly when you get
> offline-raided — the system is off too. 1 vCPU / 1 GB RAM is plenty.

> 🇹🇷 Türkçe: [DEPLOY.tr.md](DEPLOY.tr.md)

---

## Path A — Docker (recommended)

```bash
git clone <repo> raid-sentinel && cd raid-sentinel
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

The panel binds to `127.0.0.1:8787` on the host. Reach it from your own
machine over SSH:

```bash
ssh -L 8787:localhost:8787 user@server
```

Then open `http://localhost:8787/`.

```bash
docker compose logs -f
```

## Path B — systemd

```bash
sudo useradd --system --create-home --home-dir /opt/raid-sentinel sentinel
sudo -u sentinel git clone <repo> /opt/raid-sentinel
cd /opt/raid-sentinel
sudo -u sentinel python3.12 -m venv .venv
sudo -u sentinel .venv/bin/pip install .
sudo -u sentinel mkdir -p data
sudo -u sentinel cp .env.example .env

sudo cp deploy/raid-sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raid-sentinel
```

```bash
systemctl status raid-sentinel
journalctl -u raid-sentinel -f
```

---

## Pairing on a headless server

Pairing needs a browser and a callback server on `localhost:3000`. Your
server has no browser, so pick one of these.

### Option 1 — pair on your own machine, copy the file *(easiest)*

On your desktop:

```bash
pip install .
sentinel pair
```

Complete the Steam sign-in, then copy the result to the server:

```bash
scp data/rustplus.config.json user@server:/opt/raid-sentinel/data/
```

For Docker the target is `./data/rustplus.config.json`. Restart the service.

### Option 2 — SSH tunnel

From your machine:

```bash
ssh -L 3000:localhost:3000 user@server
```

In that same session:

```bash
cd /opt/raid-sentinel && .venv/bin/sentinel pair
```

It prints a URL but cannot open a browser. Open `http://localhost:3000/` in
**your** browser; the tunnel carries the token back.

Inside Docker the tunnel terminates on the host, not in the container, so
publish the callback port and bind it wide:

```bash
docker compose run --rm -p 127.0.0.1:3000:3000 sentinel \
  sentinel pair --host 0.0.0.0
```

**Option 1 is less hassle.** Pairing is a one-time thing, until the wipe.

---

## Each person pairs their own account

If a friend runs their own copy, they must run `sentinel pair` with **their
own Steam account**. Credentials are tied to one Rust account and cannot be
shared — a copied `rustplus.config.json` would deliver *your* alarms to
*their* server.

One instance can, however, notify many people: see the **Team** tab.

---

## After install

1. Open the panel (SSH tunnel) → **Setup** tab shows a live checklist
2. **Settings** → add a notification channel → *"Test notifications"*
3. In game: `ESC → Rust+ → Pair with Server`, then **Pair** each Smart Alarm
4. **Base** → draw the zones
5. **Devices** → *"Test trigger"* to verify the whole chain

Detailed test scenarios: [TESTING.md](TESTING.md).

---

## Backups

Only `data/` matters:

```bash
tar czf sentinel-backup-$(date +%F).tar.gz data/
```

`rustplus.config.json` holds a token derived from your Steam session — do
not share the archive.

## External monitoring

`/health` returns **503** when unhealthy. Point an uptime service at it;
silent death is how alarm systems fail. Since the panel is local-only,
either run the monitor on the same host against
`http://127.0.0.1:8787/health`, or expose just that path through an
authenticated reverse proxy.

## Updating

```bash
git pull
docker compose up -d --build                          # Docker
sudo -u sentinel .venv/bin/pip install --upgrade .    # systemd
sudo systemctl restart raid-sentinel
```

`data/` is untouched. Database migrations run on startup.

## Security notes

- The panel has **no session management**. It binds to `127.0.0.1` by
  default and should stay that way; use an SSH tunnel.
- If you must expose it, set `PANEL_TOKEN` in Settings *and* put an
  authenticated reverse proxy in front. Twilio credentials and webhooks are
  editable from the panel.
- Keep `data/` at `700`, owned by the service user.
- The Docker image runs as a non-root user (`uid 10001`).

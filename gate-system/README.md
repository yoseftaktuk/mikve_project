# Gate System (Raspberry Pi Entrance Control) — Microservices Starter

Production-ready starter for a physical entrance gate with RFID/NFC access, balance management, payments (cash/card), and Raspberry Pi hardware control.

## Architecture

- **Frontend**: React + TypeScript (Vite), Axios, gate monitor + management panel
- **Backend**: 3 Python **FastAPI** microservices (async), REST, OpenAPI
- **Database**: PostgreSQL (normalized tables, separated by service schemas)
- **Cache/Broker**: Redis (caching + pub/sub for real-time events)
- **Reverse proxy**: Nginx (single public entrypoint)
- **Management**: PIN-protected panel for chip top-up, fingerprint enrollment, and manual door open
- **Fingerprint**: AS608/R307 sensor — enroll a named person, then confirm each entry from the dashboard
- **Hardware**: Raspberry Pi GPIO/serial abstraction with **mock mode**

Services:

- `fingerprints-service`: chip registry, balances, chip history (fingerprints are virtual chips `FP-<slot>`)
- `hardware-service`: RFID reader + coin acceptor + relay lock + fingerprint sensor + health monitoring
- `access-control-service`: orchestrates entrance authorization, logs access, real-time events
- `payment-service`: cash stub + **Nedarim Plus** credit-card balance top-up (callback is the only credit path)
- `cloudflared` (optional compose profile): Cloudflare Tunnel so Nedarim can reach the callback over HTTPS

## Quickstart (Docker)

1. Copy env examples:

```bash
cd gate-system
cp .env.example .env
cp services/fingerprints-service/.env.example services/fingerprints-service/.env
cp services/hardware-service/.env.example services/hardware-service/.env
cp services/access-control-service/.env.example services/access-control-service/.env
cp services/payment-service/.env.example services/payment-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

> `.env` files are ignored by git and never committed — they hold machine-local
> values and secrets. Only the `.env.example` files are tracked.

2. Start everything:

```bash
docker compose up --build
```

`docker-compose.override.yml` is merged automatically. It mounts local source and enables **live reload** (Python `uvicorn --reload`, dashboard Vite HMR). Edit code on your machine and the containers pick it up without rebuilding.

Production-like run (no live reload / no source mounts):

```bash
docker compose -f docker-compose.yml up --build
```

3. Open:

- **Dashboard**: `http://localhost/`
- **OpenAPI**:
  - `http://localhost/api/fingerprints/docs`
  - `http://localhost/api/hardware/docs`
  - `http://localhost/api/access/docs`

## Real-time events

- Services publish events to Redis channels (e.g. `hardware.events`, `access.events`).
- `access-control-service` exposes WebSockets at `/ws/events` and forwards pub/sub events to connected dashboards.

Fingerprint-related events:

| Event | Channel | Meaning |
|-------|---------|---------|
| `fingerprint.scan` | `hardware.events` | sensor matched a stored template (`slot`, `confidence`) |
| `fingerprint.unmatched` | `hardware.events` | a finger was read but matched nothing |
| `fingerprint.enroll_progress` | `hardware.events` | enrollment step (`place_finger`, `remove_finger`, `place_again`, `stored`, `duplicate`, …) |
| `fingerprint.enrolled` | `hardware.events` | template stored in the sensor (`session_id`, `slot`) |
| `access.pending` | `access.events` | scan resolved to a person, waiting for confirmation (nothing charged yet) |
| `access.pending_cleared` | `access.events` | approval expired, cancelled, or replaced by a newer scan |
| `fingerprint.registered` | `access.events` | virtual chip `FP-<slot>` created/renamed with its balance |

## הפעלה על Raspberry Pi

מדריך להרצת מערכת השער על Raspberry Pi (דגם B או חדש יותר) עם מטבעון, קורא צ'יפים וריליי לדלת.

### מה צריך

| רכיב | פירוט |
|------|--------|
| Raspberry Pi | מודל 3 / 4 / Zero 2 W (מומלץ Raspberry Pi OS 64-bit) |
| כרטיס SD | 16 GB ומעלה |
| רשת | Ethernet או Wi-Fi (לגישה לדשבורד מהדפדפן) |
| מטבעון | פלט פולסים ל-GPIO |
| ריליי לדלת | מחובר ל-GPIO (נעול = פין מונע LOW; פתיחה = float כמו ניתוק IN1) |
| קורא RFID (אופציונלי) | USB serial (`/dev/ttyUSB0`) |
| חיישן טביעת אצבע (אופציונלי) | AS608 / R307 ב-UART (`/dev/serial0` או USB-TTL) |

### חיווט GPIO (מצב BCM)

| פין BCM | תפקיד |
|---------|--------|
| **17** | קלט פולסים מהמטבעון (`FALLING`, pull-up פנימי) |
| **22** | ריליי דלת (פיזי: פין **15**). Idle = OUTPUT LOW (נעול); פתיחה = INPUT float (כמו ניתוק IN1) |

חיווט מנעול fail-safe (מופעל = נעול) עם מודול ריליי פעיל-LOW:

- `COM` ← 12V+, `NO` ← Lock(+), Lock(−) ← 12V−
- `IN1` ← BCM 22 (פיזי 15), `VCC` ← 5V, `GND` משותף עם ה-Pi והספק
- במנוחה הפין מונע LOW (כמו IN1 מחובר) → המנעול נעול
- בפתיחה הפין ב-float (כמו ניתוק IN1) → המנעול נפתח

### חיווט חיישן טביעת אצבע (AS608 / R307)

החיישן עובד ב-**3.3V** בלבד — חיבור ל-5V ישרוף אותו.

| חוט בחיישן | חיבור ב-Pi |
|------------|-------------|
| VCC (אדום) | 3.3V (פין 1) |
| GND (שחור) | GND (פין 6) |
| TX (ירוק) | RX = BCM 15 / GPIO15 (פין 10) |
| RX (לבן) | TX = BCM 14 / GPIO14 (פין 8) |

שים לב ל-**הצלבה**: TX של החיישן ל-RX של ה-Pi ולהיפך.

שתי אפשרויות חיבור:

- **UART של ה-Pi** (`/dev/serial0`): הרץ `sudo raspi-config` → *Interface Options* → *Serial Port* → כבה shell על הפורט, הפעל את החומרה, ואתחל.
- **מתאם USB-TTL** (`/dev/ttyUSB1`): פשוט יותר, לא דורש raspi-config, ומשאיר את `/dev/ttyUSB0` לקורא ה-RFID.

הגדרה ב-`services/hardware-service/.env` (ריק = מנוטרל):

```env
FINGERPRINT_SERIAL_PORT=/dev/serial0
FINGERPRINT_BAUDRATE=57600
```

בדיקה שהחיישן זוהה:

```bash
curl http://<PI-IP>/api/hardware/status   # fingerprint_reader_connected: true
```

מיפוי פולסים למטבעות (כמו בקוד המקורי):

| פולסים | סכום |
|--------|------|
| 1 | ₪0.10 |
| 5 | ₪1 |
| 10 | ₪5 |
| 15 | ₪10 |

### 1. התקנת Docker על ה-Pi

```bash
# עדכון מערכת
sudo apt update && sudo apt upgrade -y

# התקנת Docker (הרשמי)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# התנתק והתחבר מחדש כדי שהקבוצה docker תיכנס לתוקף
```

ודא ש-`docker compose` עובד:

```bash
docker compose version
```

### 2. העתקת הפרויקט והגדרות

```bash
cd ~
git clone <your-repo-url> gate-system
cd gate-system
```

העתק קבצי `.env`:

```bash
cp .env.example .env
cp services/fingerprints-service/.env.example services/fingerprints-service/.env
cp services/hardware-service/.env.example services/hardware-service/.env
cp services/access-control-service/.env.example services/access-control-service/.env
cp services/payment-service/.env.example services/payment-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

### 3. הגדרת חומרה (חובה)

ערוך `services/hardware-service/.env`:

```env
HARDWARE_MODE=rpi

# פינים (BCM)
COIN_ACCEPTOR_GPIO_PIN=17
DOOR_RELAY_GPIO_PIN=22
DOOR_RELAY_IDLE_LEVEL=low   # driven while locked; unlock floats the pin (unplug IN1)

# קורא RFID USB (אם קיים)
RFID_SERIAL_PORT=/dev/ttyUSB0
RFID_BAUDRATE=9600
```

> **זמן פתיחת הדלת** ו**מחיר כניסה** מוגדרים במקום אחד בלבד —  
> `services/access-control-service/.env`:

```env
ENTRANCE_FEE_CENTS=500    # באגורות: 500 = ₪5
DOOR_UNLOCK_SECONDS=5     # כמה שניות הדלת פתוחה
CASH_SESSION_TIMEOUT_SECONDS=20  # איפוס תשלום מזומן חלקי אם לא נכנס מטבע נוסף
MANAGEMENT_PIN=1234       # קוד סודי לדף ניהול (הטענת צ'יפ / פתיחת דלת / רישום אצבע)
FINGERPRINT_APPROVAL_TIMEOUT_SECONDS=25  # כמה זמן חלון האישור אחרי סריקת אצבע
```

אחרי שינוי מחיר, זמן דלת או קוד ניהול:

```bash
docker compose up -d access-control-service
```

### 4. הפעלת המערכת על ה-Pi

הפעלה עם גישה ל-GPIO מתוך Docker:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d --build
```

בדיקת סטטוס:

```bash
docker compose ps
docker compose logs hardware-service --tail 30
docker compose logs access-control-service --tail 30
```

ודא ש-`hardware-service` מדווח `mode=rpi` ושאין שגיאות GPIO:

```bash
curl http://localhost/api/hardware/status
```

### 5. פתיחת הדשבורד

בדפדפן (מה-Pi או ממחשב ברשת):

```
http://<PI-IP>/
```

לדוגמה: `http://192.168.1.50/`

המסך מאזין אוטומטית לצ'יפים ומזומן. אין צורך בהרשמה — כניסה מתבצעת בדלת בלבד.

### 6. רישום צ'יפ (פעם אחת לכל צ'יפ)

צ'יפ חייב להיות רשום במערכת עם יתרה לפני שימוש:

```bash
# החלף <PI-IP> ו-<UID> בערכים שלך
curl -X POST http://<PI-IP>/api/fingerprints/fingerprints \
  -H "Content-Type: application/json" \
  -d '{"uid": "YOUR-CHIP-UID"}'

# טעינת יתרה (1000 אגורות = ₪10)
curl -X POST http://<PI-IP>/api/fingerprints/fingerprints/<CHIP_ID>/balance/adjust \
  -H "Content-Type: application/json" \
  -d '{"delta_cents": 1000, "reason": "topup", "description": "initial balance"}'
```

### 7. רישום טביעת אצבע

בדשבורד: **🫆 רישום אצבע** ← הזן את קוד הניהול (`MANAGEMENT_PIN`).

1. הזן **שם מלא** ואופציונלית **יתרה התחלתית** (₪), ולחץ **התחל רישום**.
2. עקוב אחרי ההוראות על המסך — הן מגיעות מהחיישן בזמן אמת:
   `הנח את האצבע` → `הרם את האצבע` → `הנח את אותה אצבע שוב`.
3. בסיום נוצר כרטיס אישי בשם שהזנת. אם האצבע כבר רשומה, המסך מציג `האצבע הזו כבר רשומה` ולא נוצר כרטיס כפול.

כל אצבע נשמרת בחיישן עצמו (ההשוואה מתבצעת שם), והמערכת שומרת רק **כרטיס וירטואלי** עם ה-UID `FP-<slot>` — לכן טעינת יתרה, היסטוריה ודף הניהול עובדים עליה בדיוק כמו על צ'יפ רגיל:

```bash
# טעינת יתרה לאצבע שנרשמה בחריץ 12 (דרך דף הניהול או ישירות)
curl -X POST http://<PI-IP>/api/access/management/chip/topup \
  -H "Content-Type: application/json" \
  -H "X-Management-Token: <TOKEN>" \
  -d '{"uid": "FP-012", "amount_cents": 5000}'
```

### 8. כניסה עם טביעת אצבע (דורשת אישור)

בשונה מצ'יפ, סריקת אצבע **לא מחייבת מיד**:

1. הנכנס מצמיד אצבע לחיישן.
2. בדשבורד נפתחת חלונית עם **השם** של הנרשם, היתרה, עלות הכניסה וספירה לאחור.
3. לחיצה על **שלם ופתח דלת** מנכה את עלות הכניסה ופותחת את הדלת. **ביטול** סוגר בלי לחייב.
4. אם לא נלחץ כלום תוך `FINGERPRINT_APPROVAL_TIMEOUT_SECONDS` (ברירת מחדל 25 שניות) האישור פג, ואין חיוב.

אצבע שלא מזוהה, כרטיס חסום או יתרה חסרה — מוצגים כהודעת דחייה בלי חלונית אישור.

### 9. איך זה עובד בפועל

```
מטבעון (GPIO 17)  ──► hardware-service ──► Redis ──► access-control-service
קורא RFID (USB)   ──►       │                              │
חיישן אצבע (UART) ──►       │                              ├─ מספיק מזומן? → פתיחת דלת
                            │                              ├─ צ'יפ תקין?   → ניכוי יתרה → פתיחת דלת
                            │                              └─ אצבע זוהתה?  → אישור בדשבורד → ניכוי → פתיחה
                            ▼
                    ריליי דלת (GPIO 22) ── float (כמו ניתוק IN1) ל-DOOR_UNLOCK_SECONDS שניות
```

- **מזומן**: המערכת צוברת מטבעות עד `ENTRANCE_FEE_CENTS`, ואז פותחת את הדלת. אם נכנס סכום חלקי ולא נוסף מטבע תוך `CASH_SESSION_TIMEOUT_SECONDS` (ברירת מחדל 20), הסכום מתאפס.
- **צ'יפ**: ניכוי עלות כניסה מהיתרה; אם יש מספיק כסף — הדלת נפתחת.
- **טביעת אצבע**: זיהוי בחיישן → חלונית אישור עם השם → רק לחיצה על הכפתור מחייבת ופותחת.
- **דשבורד**: מציג הודעות, יתרת צ'יפ וסכום מזומן שנצבר.

### 10. הפעלה אוטומטית אחרי אתחול

```bash
cd ~/gate-system
sudo tee /etc/systemd/system/gate-system.service > /dev/null <<'EOF'
[Unit]
Description=Gate System
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/pi/gate-system
ExecStart=/usr/bin/docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable gate-system
sudo systemctl start gate-system
```

> עדכן את `WorkingDirectory` אם הפרויקט לא ב-`/home/pi/gate-system`.

### 11. פתרון בעיות נפוצות

| בעיה | מה לבדוק |
|------|-----------|
| מטבעות לא מזוהים | חיווט לפין 17, `HARDWARE_MODE=rpi`, הרצה עם `docker-compose.pi.yml` |
| דלת לא נפתחת | פין 22, ריליי, `docker compose logs hardware-service` |
| צ'יפ נדחה | הצ'יפ רשום? יש יתרה ≥ `ENTRANCE_FEE_CENTS`? |
| RFID לא עובד | `ls /dev/ttyUSB*`, עדכן `RFID_SERIAL_PORT` ב-`.env` |
| אצבע לא נסרקת | `FINGERPRINT_SERIAL_PORT` נכון? החיישן ב-3.3V? TX/RX מוצלבים? |
| חלונית האישור לא נפתחת | האצבע רשומה (`FP-<slot>` קיים)? יש יתרה מספקת? בדוק `docker compose logs access-control-service` |
| שגיאת GPIO ב-Docker | ודא `privileged: true` ו-`/dev/gpiomem` ב-`deploy/docker-compose.pi.yml` |
| שינוי מחיר לא נכנס | ערוך `access-control-service/.env` והפעל מחדש את השירות |

### פיתוח על מחשב (בלי Pi)

להרצה מקומית עם סימולציה (בלי GPIO אמיתי):

```bash
# services/hardware-service/.env
HARDWARE_MODE=mock

docker compose up --build
```

בדשבורד: **כלי פיתוח (סימולציה)** — סימולציית אצבע / מזומן.

סימולציית טביעת אצבע (mock בלבד) — החריץ שהוחזר בעת הרישום, למשל 1:

```bash
curl -X POST http://localhost/api/hardware/dev/fingerprint/scan \
  -H "Content-Type: application/json" -d '{"slot": 1}'

# אצבע שלא מזוהה
curl -X POST http://localhost/api/hardware/dev/fingerprint/scan \
  -H "Content-Type: application/json" -d '{"slot": null}'
```

ב-mock, **רישום אצבע** מתקדם אוטומטית בין השלבים (שנייה לכל שלב) ומקצה חריץ עולה, כך שאפשר לבדוק את כל הזרימה בלי חיישן.

## Split deployment (Pi edge + LAN backend)

Run only **hardware-service** and the **dashboard** on the Raspberry Pi. Put Postgres, Redis, chip/access/payment (and main Nginx) on another LAN PC to reduce Pi load.

```
Raspberry Pi (edge)              LAN server (backend)
---------------------            -------------------------
hardware-service (GPIO)   <----> Redis pub/sub + HTTP door open
dashboard + thin Nginx           postgres, redis, chip, access,
                                 payment, nginx, dashboard
```

### 1. Configure the LAN server

```bash
cd gate-system
cp .env.example .env
# Set EDGE_HARDWARE_HOST=<PI_LAN_IP>  (e.g. 192.168.1.50)
cp services/fingerprints-service/.env.example services/fingerprints-service/.env
cp services/payment-service/.env.example services/payment-service/.env
cp services/access-control-service/.env.example services/access-control-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

In `services/access-control-service/.env`:

```env
HARDWARE_SERVICE_URL=http://<PI_LAN_IP>:8000
FINGERPRINTS_SERVICE_URL=http://fingerprints-service:8000
```

Start the backend:

```bash
docker compose -f deploy/docker-compose.server.yml --project-directory . --env-file .env up -d --build
```

Redis is published on LAN port `6379` (`REDIS_LAN_PORT`). Restrict it with a firewall to your LAN only — do not expose Redis to the public internet.

### 2. Configure the Raspberry Pi edge

```bash
cd gate-system
cp deploy/.env.edge.example .env.edge
# Set SERVER_LAN_IP=<SERVER_LAN_IP>  (e.g. 192.168.1.10)
cp services/hardware-service/.env.example services/hardware-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

In `services/hardware-service/.env`:

```env
HARDWARE_MODE=rpi
REDIS_URL=redis://<SERVER_LAN_IP>:6379/0
```

Start the edge stack (GPIO mounts included):

```bash
docker compose -f deploy/docker-compose.edge.yml --project-directory . --env-file .env.edge up -d --build
```

### 3. Auto-start on boot (systemd)

Units live in `deploy/systemd/` and only cover **split deploy**.

**On the LAN server** (after `.env` is configured):

```bash
cd gate-system
chmod +x deploy/systemd/install.sh
sudo ./deploy/systemd/install.sh server
```

**On the Raspberry Pi** (after `.env.edge` and hardware `.env` are configured):

```bash
cd gate-system
chmod +x deploy/systemd/install.sh
sudo ./deploy/systemd/install.sh edge
```

The install script sets `WorkingDirectory` to your current `gate-system` path, enables the unit, and starts it.

Useful commands:

```bash
sudo systemctl status gate-system-edge    # Pi
sudo systemctl status gate-system-server  # server
sudo journalctl -u gate-system-edge -e
```

First build still needs a manual `--build` once (or after dependency changes). Daily boot only runs `up -d`.

### 4. Open the UI

- Kiosk / Pi: `http://<PI_LAN_IP>/` (edge Nginx proxies business APIs to the server)
- Server / admin PC: `http://<SERVER_LAN_IP>/`

### Security notes

- Gate opening depends on LAN connectivity (Redis events + HTTP door command).
- Do not publish Redis (`6379`) or hardware HTTP (`8000`) to the public internet without VPN/firewall rules.
- Prefer a dedicated LAN VLAN for the gate Pi and server.

### Single-host note

Local development and all-in-one Pi installs still use:

```bash
docker compose up --build
# or with GPIO (explicit -f skips docker-compose.override.yml live-reload):
docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d --build
```

**Live reload (localhost):** `docker compose up` auto-loads `docker-compose.override.yml`. Change Python or dashboard files and services reload in place. Rebuild only when dependencies (`requirements.txt` / `package.json`) change.

## Mock card top-up (local dev)

For local Docker without Nedarim credentials or Cloudflare Tunnel:

```env
# services/payment-service/.env
PAYMENT_MODE=mock
# PUBLIC_BASE_URL not required
# NEDARIM_MOSAD / NEDARIM_API_VALID not required
```

```bash
docker compose up --build
```

1. Open `http://localhost` (localhost works in mock mode — no Nedarim iframe).
2. Trigger a fingerprint top-up (insufficient balance) → **Credit card**.
3. Pick an amount → click **סימולציית תשלום** (dev mock).
4. Balance credits via the same server callback path as production.

Verify: `GET /api/payments/healthz` → `"payment_mode": "mock"`.

For real Nedarim clearing, set `PAYMENT_MODE=nedarim` and configure the tunnel below.

## Nedarim Plus card top-up (Cloudflare Tunnel)

Card top-up needs a **public HTTPS origin**:

1. The kiosk page must not be plain `localhost` (Nedarim `postMessage` requires a real domain).
2. Nedarim posts the CallBack once from `18.196.146.117` / `18.194.219.73`.

### Named tunnel (production)

1. In Cloudflare Zero Trust → Networks → Tunnels, create a tunnel and a public hostname (e.g. `gate-pay.example.org`) pointing at `http://nginx:80`. Prefer exposing only that hostname; keep LAN `:80` for management.
2. Put the tunnel token in root `.env` as `CLOUDFLARE_TUNNEL_TOKEN=…`.
3. Set `PUBLIC_BASE_URL=https://gate-pay.example.org` in `services/payment-service/.env` (no trailing slash).
4. Start with the profile:

```bash
docker compose --profile tunnel up -d --build
# or on the LAN server compose:
docker compose -f deploy/docker-compose.server.yml --project-directory . --env-file .env --profile tunnel up -d --build
```

5. Confirm `GET /api/payments/healthz` shows `payment_mode: "nedarim"`, `public_base_url_set: true`, and `nedarim_configured: true`.

Path-filtered ingress example: `deploy/cloudflared/config.example.yml`.

### Quick tunnel (one-shot smoke)

```bash
docker compose --profile quick-tunnel up cloudflared-quick
# Wait for a line like: https://xxxx.trycloudflare.com
```

Copy that URL into `services/payment-service/.env` as `PUBLIC_BASE_URL`, restart `payment-service`, then create a ₪20 top-up from the kiosk (or `POST /api/payments/card-topups`). The URL changes every restart — use a named tunnel for real use.

### Kiosk flow reminder

Fingerprint with balance &lt; entrance fee → Coins / Credit card / Cancel → presets ₪20 / ₪50 / ₪100 → Nedarim iframe → CallBack credits chip balance → scan again to enter. Coins still pay the door fee in-session; they do not top up balance.

## Folder structure

```
gate-system/
  apps/
    dashboard/                 # React TS app
  services/
    access-control-service/
    fingerprints-service/
    hardware-service/
    payment-service/           # Nedarim Plus top-up + legacy charge stub
  shared/
    py/                        # shared Python package (settings, errors, logging)
  deploy/
    nginx/
    postgres/
    cloudflared/               # example tunnel ingress
    docker-compose.server.yml  # LAN backend (no hardware)
    docker-compose.edge.yml    # Pi edge (hardware + dashboard)
    docker-compose.pi.yml      # GPIO override for single-host Pi
    systemd/                   # split-deploy boot units (edge + server)
  docker-compose.override.yml  # Local live reload (auto-merged on `docker compose up`)
  diagrams/                    # Mermaid + UML-like docs
```

## Testing

Unit tests per service (each service has its own `pytest.ini`):

```bash
cd services/access-control-service && pytest   # fingerprint approval/scan/enrollment logic
cd services/hardware-service && pytest         # sensor driver against a fake AS608
cd services/payment-service && pytest          # Nedarim create/callback/idempotency
```

Dashboard checks:

```bash
cd apps/dashboard && npm run lint && npm run build
```

## Next steps (typical)

- Add Alembic migrations per service schema
- Harden production settings (TLS, secrets manager, HSTS, rate limiting config)
- Optional: set Nedarim `CallBackMailError` so failed callback delivery emails someone on staff


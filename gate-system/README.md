# Gate System (Raspberry Pi Entrance Control) — Microservices Starter

Production-ready starter for a physical entrance gate with RFID/NFC access, balance management, payments (cash/card), and Raspberry Pi hardware control.

## Architecture

- **Frontend**: React + TypeScript (Vite), Axios, gate monitor + management panel
- **Backend**: 3 Python **FastAPI** microservices (async), REST, OpenAPI
- **Database**: PostgreSQL (normalized tables, separated by service schemas)
- **Cache/Broker**: Redis (caching + pub/sub for real-time events)
- **Reverse proxy**: Nginx (single public entrypoint)
- **Management**: PIN-protected panel for chip top-up and manual door open
- **Hardware**: Raspberry Pi GPIO/serial abstraction with **mock mode**

Services:

- `chip-service`: chip registry, balances, chip history
- `hardware-service`: RFID reader + coin acceptor + relay lock + health monitoring
- `access-control-service`: orchestrates entrance authorization, logs access, real-time events

## Quickstart (Docker)

1. Copy env examples:

```bash
cd gate-system
cp .env.example .env
cp services/chip-service/.env.example services/chip-service/.env
cp services/hardware-service/.env.example services/hardware-service/.env
cp services/access-control-service/.env.example services/access-control-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

2. Start everything:

```bash
docker compose up --build
```

3. Open:

- **Dashboard**: `http://localhost/`
- **OpenAPI**:
  - `http://localhost/api/chips/docs`
  - `http://localhost/api/hardware/docs`
  - `http://localhost/api/access/docs`

## Real-time events

- Services publish events to Redis channels (e.g. `hardware.events`, `access.events`).
- `access-control-service` exposes WebSockets at `/ws/events` and forwards pub/sub events to connected dashboards.

## הפעלה על Raspberry Pi

מדריך להרצת מערכת השער על Raspberry Pi (דגם B או חדש יותר) עם מטבעון, קורא צ'יפים וריליי לדלת.

### מה צריך

| רכיב | פירוט |
|------|--------|
| Raspberry Pi | מודל 3 / 4 / Zero 2 W (מומלץ Raspberry Pi OS 64-bit) |
| כרטיס SD | 16 GB ומעלה |
| רשת | Ethernet או Wi-Fi (לגישה לדשבורד מהדפדפן) |
| מטבעון | פלט פולסים ל-GPIO |
| ריליי לדלת | מחובר ל-GPIO (פתיחה ב-LOW / 0V; idle ב-HIGH) |
| קורא RFID (אופציונלי) | USB serial (`/dev/ttyUSB0`) |

### חיווט GPIO (מצב BCM)

| פין BCM | תפקיד |
|---------|--------|
| **17** | קלט פולסים מהמטבעון (`FALLING`, pull-up פנימי) |
| **22** | פלט ריליי לדלת (LOW/0V = דלת פתוחה; HIGH = נעול). פיזי: פין 15 |

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
cp services/chip-service/.env.example services/chip-service/.env
cp services/hardware-service/.env.example services/hardware-service/.env
cp services/access-control-service/.env.example services/access-control-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

### 3. הגדרת חומרה (חובה)

ערוך `services/hardware-service/.env`:

```env
HARDWARE_MODE=rpi

# פינים (BCM)
COIN_ACCEPTOR_GPIO_PIN=17
DOOR_RELAY_GPIO_PIN=22
DOOR_RELAY_ACTIVE_HIGH=false   # false = פתיחה ב-LOW (0V); true = פתיחה ב-HIGH

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
MANAGEMENT_PIN=1234       # קוד סודי לדף ניהול (הטענת צ'יפ / פתיחת דלת)
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
curl -X POST http://<PI-IP>/api/chips/chips \
  -H "Content-Type: application/json" \
  -d '{"uid": "YOUR-CHIP-UID"}'

# טעינת יתרה (1000 אגורות = ₪10)
curl -X POST http://<PI-IP>/api/chips/chips/<CHIP_ID>/balance/adjust \
  -H "Content-Type: application/json" \
  -d '{"delta_cents": 1000, "reason": "topup", "description": "initial balance"}'
```

### 7. איך זה עובד בפועל

```
מטבעון (GPIO 17) ──► hardware-service ──► Redis ──► access-control-service
קורא RFID (USB)   ──►       │                              │
                              │                              ├─ מספיק מזומן? → פתיחת דלת
                              │                              └─ צ'יפ תקין?  → ניכוי יתרה → פתיחת דלת
                              ▼
                    ריליי דלת (GPIO 22) ── LOW/0V ל-DOOR_UNLOCK_SECONDS שניות
```

- **מזומן**: המערכת צוברת מטבעות עד `ENTRANCE_FEE_CENTS`, ואז פותחת את הדלת. אם נכנס סכום חלקי ולא נוסף מטבע תוך `CASH_SESSION_TIMEOUT_SECONDS` (ברירת מחדל 20), הסכום מתאפס.
- **צ'יפ**: ניכוי עלות כניסה מהיתרה; אם יש מספיק כסף — הדלת נפתחת.
- **דשבורד**: מציג הודעות, יתרת צ'יפ וסכום מזומן שנצבר.

### 8. הפעלה אוטומטית אחרי אתחול

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

### 9. פתרון בעיות נפוצות

| בעיה | מה לבדוק |
|------|-----------|
| מטבעות לא מזוהים | חיווט לפין 17, `HARDWARE_MODE=rpi`, הרצה עם `docker-compose.pi.yml` |
| דלת לא נפתחת | פין 22, ריליי, `docker compose logs hardware-service` |
| צ'יפ נדחה | הצ'יפ רשום? יש יתרה ≥ `ENTRANCE_FEE_CENTS`? |
| RFID לא עובד | `ls /dev/ttyUSB*`, עדכן `RFID_SERIAL_PORT` ב-`.env` |
| שגיאת GPIO ב-Docker | ודא `privileged: true` ו-`/dev/gpiomem` ב-`deploy/docker-compose.pi.yml` |
| שינוי מחיר לא נכנס | ערוך `access-control-service/.env` והפעל מחדש את השירות |

### פיתוח על מחשב (בלי Pi)

להרצה מקומית עם סימולציה (בלי GPIO אמיתי):

```bash
# services/hardware-service/.env
HARDWARE_MODE=mock

docker compose up --build
```

בדשבורד: **כלי פיתוח (סימולציה)** — סימולציית צ'יפ / מזומן.

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
cp services/chip-service/.env.example services/chip-service/.env
cp services/payment-service/.env.example services/payment-service/.env
cp services/access-control-service/.env.example services/access-control-service/.env
cp apps/dashboard/.env.example apps/dashboard/.env
```

In `services/access-control-service/.env`:

```env
HARDWARE_SERVICE_URL=http://<PI_LAN_IP>:8000
CHIP_SERVICE_URL=http://chip-service:8000
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

### 3. Open the UI

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
# or with GPIO:
docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d --build
```

## Folder structure

```
gate-system/
  apps/
    dashboard/                 # React TS app
  services/
    access-control-service/
    chip-service/
    hardware-service/
  shared/
    py/                        # shared Python package (settings, errors, logging)
  deploy/
    nginx/
    postgres/
    docker-compose.server.yml  # LAN backend (no hardware)
    docker-compose.edge.yml    # Pi edge (hardware + dashboard)
    docker-compose.pi.yml      # GPIO override for single-host Pi
  diagrams/                    # Mermaid + UML-like docs
```

## Testing

- Unit tests per service: `pytest`

## Next steps (typical)

- Add Alembic migrations per service schema
- Harden production settings (TLS, secrets manager, HSTS, rate limiting config)


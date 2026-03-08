# kyuing-bot

Discord TTS bot with a Quart-based web dashboard.

## Local Docker

1. Create `.env` from `.env.example`.
2. Build and run:

```bash
docker compose up -d --build
```

3. Check logs:

```bash
docker compose logs -f app
```

## Required Environment Variables

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here
DISCORD_CLIENT_SECRET=your_discord_client_secret_here
DISCORD_REDIRECT_URI=https://your-domain.example/callback
WEB_SECRET_KEY=replace-with-a-long-random-secret
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
```

## Server Deployment

### 1. Install Docker

Ubuntu example:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

### 2. Deploy the app

```bash
git clone <repo-url>
cd kyuing-bot
cp .env.example .env
mkdir -p data
mkdir -p logs
docker compose up -d --build
```

### 3. Reverse proxy with Nginx

Ubuntu example:

```bash
sudo apt install -y nginx
sudo nano /etc/nginx/sites-available/kyuing-bot
```

Example config:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/kyuing-bot /etc/nginx/sites-enabled/kyuing-bot
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Enable HTTPS

Discord OAuth should use the same public callback URL configured in the Discord Developer Portal.

Certbot example:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example
```

After HTTPS is enabled, set:

```env
DISCORD_REDIRECT_URI=https://your-domain.example/callback
SESSION_COOKIE_SECURE=true
```

Then restart:

```bash
docker compose up -d
```

## Operations

Restart:

```bash
docker compose restart app
```

Update:

```bash
git pull
docker compose up -d --build
```

Logs:

```bash
docker compose logs -f app
```

Application log files are written to `logs/app.log` and retained for 30 days.
Daily dashboard stats are retained for 365 days.

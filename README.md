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
DISCORD_REDIRECT_URI=https://your-domain.com/callback
WEB_SECRET_KEY=replace-with-a-long-random-secret
WEB_PORT=8080
DATABASE_PATH=data/bot.db
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
docker compose up -d --build
```

### 3. Reverse proxy with Nginx

Use `deploy/nginx/kyuing-bot.conf` as a template.

Ubuntu example:

```bash
sudo apt install -y nginx
sudo cp deploy/nginx/kyuing-bot.conf /etc/nginx/sites-available/kyuing-bot
sudo ln -s /etc/nginx/sites-available/kyuing-bot /etc/nginx/sites-enabled/kyuing-bot
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Enable HTTPS

Discord OAuth should use the same public callback URL configured in the Discord Developer Portal.

Certbot example:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

After HTTPS is enabled, set:

```env
DISCORD_REDIRECT_URI=https://your-domain.com/callback
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

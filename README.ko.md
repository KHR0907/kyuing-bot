# kyuing-bot

[English](README.md)

`kyuing-bot`은 Discord TTS 봇과 웹 대시보드를 함께 제공하는 프로젝트입니다. 설정된 텍스트 채널의 메시지를 Discord 음성 채널에서 읽어주며, 기본 TTS 엔진은 Google Cloud Text-to-Speech입니다. 하나의 서버/대시보드에서 여러 Discord Bot Token을 관리하고 실행할 수 있습니다.

## 현재 아키텍처

신규 설치 직후에는 supervisor가 웹 대시보드와 모든 Discord 봇 worker를 관리합니다.

```text
Docker container: kyuing-bot
└─ supervisor: python supervisor.py
   ├─ Web dashboard on WEB_PORT
   └─ worker: python bot.py --worker --bot-id 1
```

대시보드에서 추가한 봇도 SQLite에 저장되고 같은 방식의 worker subprocess로 실행됩니다.

```text
Docker container: kyuing-bot
├─ supervisor + Web dashboard + BotProcessManager
├─ worker: python bot.py --worker --bot-id 1
└─ worker: python bot.py --worker --bot-id <BOT_ID>
```

Bot Token은 명령행 인자로 넘기지 않습니다. worker는 `--bot-id`만 받고, 토큰은 DB에서 읽습니다. 따라서 프로세스 목록에 토큰이 노출되지 않습니다.

## 주요 기능

- 지정한 텍스트 채널 메시지를 음성 채널에서 자동 재생
- Google TTS 기본 엔진 사용 (`ko-KR-Standard-A`)
- 선택적으로 Supertonic-3 엔진 사용 가능 (31개 언어 + `na` 자동 감지, `<laugh>` 등 표현 태그)
- 슬래시 명령어 기반 개인별 TTS 설정
- 봇별 TTS 채널, 키워드 치환, 발음 규칙, 사용량 통계, 대시보드 지표 분리
- 하나의 대시보드에서 멀티봇 추가/시작/중지/재시작/활성화/비활성화 관리
- Discord OAuth 기반 관리자 대시보드
- 일별 통계 스냅샷 및 애플리케이션 로그 보관
- 사운드보드: 8초 이하 음원(mp4/mp3/wav/ogg/webm)을 키워드로 등록하고 `/play`로 재생

## 슬래시 명령어

- `/join`: 현재 접속한 음성 채널로 봇 호출
- `/leave`: 봇을 음성 채널에서 내보냄
- `/stop`: 현재 재생 중인 음성 중지
- `/setchannel`: 현재 텍스트 채널을 TTS 채널로 등록
- `/unsetchannel`: 현재 텍스트 채널의 TTS 설정 해제
- `/channels`: 현재 서버에 등록된 TTS 채널 목록 확인
- `/engine`: 내 TTS 엔진 변경
- `/voice`: 현재 엔진에서 사용할 음성 선택
- `/speed`: 읽기 속도 설정
- `/lang`: Supertonic 언어 설정 (31개 언어 + `na` 자동 감지, autocomplete로 선택)
- `/quality`: Supertonic 음성 품질 설정 (추론 스텝 5 / 8 / 10 / 12, 기본값 8)
- `/settings`: 내 TTS 설정 확인
- `/voices`: 현재 엔진에서 사용 가능한 음성 목록 확인
- `/pronounce`: 키워드/발음 치환 미리보기
- `/usage`: Google TTS 월간 문자 사용량 확인
- `/sound add`: 현재 서버에 음원(8초 이하)을 키워드와 함께 등록
- `/sound remove`: 현재 서버에 등록된 음원 삭제
- `/sound list`: 사용 가능한 음원 목록 (서버 + 전역)
- `/play`: 등록된 음원을 음성 채널에서 재생

## 시스템 요구사항

- Docker 및 Docker Compose plugin
- Docker 없이 실행한다면 Python 3.11+
- FFmpeg, Docker 이미지에 포함됨
- Google TTS 사용 시 Google Cloud Text-to-Speech API Key
- RAM: Google TTS만 사용하면 보통 1 GB+로 충분, Supertonic-3 사용 시 로컬 모델 로딩 때문에 4 GB+ 권장

## fresh clone 직후 상태

이 repository를 처음 clone하면 코드와 배포 파일만 있습니다.

포함되는 것:

```text
bot.py
bot_process_manager.py
supervisor.py
dashboard_context.py
audio_scheduler.py
worker_lock.py
config.py
database.py
logging_setup.py
tts_engine.py
tts_engines/
cogs/
web/
tests/
requirements.txt
Dockerfile
docker-compose.yml
.env.example
README.md
README.ko.md
```

포함되지 않는 것:

```text
.env
data/bot.db
data/sounds/
logs/app.log
Discord Bot Token
Google TTS API Key
기존 봇 등록 정보
기존 대시보드 데이터 / TTS 채널 / 키워드 규칙 / 사용량 통계
```

최초 실행 시 SQLite가 `data/bot.db`를 자동 생성하고, `.env`의 `DISCORD_TOKEN`을 사용해서 기본 봇 1개를 seed합니다.

```text
bot_id=1
name=Default Bot
token=<.env의 DISCORD_TOKEN>
enabled=1
```

추가 봇은 이후 대시보드에서 등록해야 합니다.

## Discord 설정

### 1. Discord Application / Bot 생성

1. [Discord Developer Portal](https://discord.com/developers/applications)에 접속합니다.
2. 새 Application을 만듭니다.
3. **Bot** 메뉴에서 bot token을 생성/재발급합니다.
4. **Message Content Intent**를 켭니다. 이 봇은 채널 메시지를 읽어 TTS로 변환하므로 필수입니다.
5. bot token을 `.env`의 `DISCORD_TOKEN`에 넣습니다.

### 2. 대시보드 OAuth 설정

동일한 Discord Application에서:

1. **OAuth2** 메뉴로 이동합니다.
2. Client ID와 Client Secret을 `.env`에 입력합니다.
3. `DISCORD_REDIRECT_URI`와 정확히 같은 Redirect URI를 Discord Developer Portal에 등록합니다.

로컬 예시:

```text
http://localhost:5001/callback
```

운영 HTTPS 예시:

```text
https://your-domain.example/callback
```

### 3. 봇을 Discord 서버에 초대

OAuth2 URL Generator에서 아래 scopes를 선택합니다.

```text
bot
applications.commands
```

권장 권한:

```text
View Channels
Send Messages
Read Message History
Connect
Speak
Use Voice Activity
```

위 권한 조합에 사용 가능한 permission integer 예시:

```text
36768768
```

## Google Cloud Text-to-Speech 설정

현재 기본 엔진은 Google TTS입니다. 사용하려면:

1. Google Cloud 프로젝트를 생성하거나 선택합니다.
2. **Cloud Text-to-Speech API**를 활성화합니다.
3. API Key를 생성합니다.
4. `.env`의 `GOOGLE_TTS_API_KEY`에 입력합니다.

Google TTS 입력 제한은 Google API 기준입니다.

```text
요청당 5,000 UTF-8 bytes
```

대략:

- 영어/숫자/일반 ASCII 기호: 약 5,000자
- 한글: UTF-8에서 보통 글자당 3 bytes이므로 약 1,666자

## Docker 빠른 시작

### 1. repository clone

```bash
git clone https://github.com/KHR0907/kyuing-bot.git
cd kyuing-bot
```

### 2. `.env` 생성

```bash
cp .env.example .env
```

`.env`를 열어서 실제 값을 입력합니다.

로컬 개발 예시:

```env
APP_ENV=production
DISCORD_TOKEN=your_discord_bot_token
DISCORD_CLIENT_ID=your_discord_client_id
DISCORD_CLIENT_SECRET=your_discord_client_secret
DISCORD_REDIRECT_URI=http://localhost:5001/callback
DASHBOARD_ADMIN_IDS=your_discord_user_id
WEB_SECRET_KEY=replace_with_a_long_random_string
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=Lax
GOOGLE_TTS_API_KEY=your_google_tts_api_key
```

운영 HTTPS 환경에서는 보통 아래처럼 둡니다.

```env
DISCORD_REDIRECT_URI=https://your-domain.example/callback
SESSION_COOKIE_SECURE=true
```

### 3. 데이터 디렉터리 생성

```bash
mkdir -p data logs
```

### 4. 빌드 및 실행

```bash
docker compose up -d --build
```

### 5. 실행 확인

```bash
docker compose ps
docker compose logs -f app
```

대시보드 접속:

```text
http://localhost:5001/
```

서버에서 직접 접속한다면:

```text
http://<server-ip>:5001/
```

reverse proxy를 사용하는 경우 `127.0.0.1:5001`로 proxy하면 됩니다.

## 필수 환경 변수

```env
APP_ENV=production
DISCORD_TOKEN=your_discord_bot_token
DISCORD_CLIENT_ID=your_discord_client_id
DISCORD_CLIENT_SECRET=your_discord_client_secret
DISCORD_REDIRECT_URI=https://your-domain.example/callback
DASHBOARD_ADMIN_IDS=123456789012345678,234567890123456789
WEB_SECRET_KEY=replace_with_a_long_random_string
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
GOOGLE_TTS_API_KEY=your_google_tts_api_key
```

## 환경 변수 설명

- `DISCORD_TOKEN`: 최초/default Discord 봇 토큰입니다.
- `DISCORD_CLIENT_ID`: 대시보드 로그인을 위한 Discord OAuth client ID입니다.
- `DISCORD_CLIENT_SECRET`: 대시보드 로그인을 위한 Discord OAuth client secret입니다.
- `DISCORD_REDIRECT_URI`: Discord Developer Portal에 등록한 OAuth callback URL입니다.
- `DASHBOARD_ADMIN_IDS`: 대시보드 접근을 허용할 Discord 사용자 ID 목록입니다. 쉼표로 구분합니다.
- `WEB_SECRET_KEY`: 웹 세션 서명용 secret입니다. 운영에서는 충분히 긴 랜덤 문자열을 사용하세요.
- `WEB_PORT`: 대시보드 포트입니다. Docker Compose는 host `WEB_PORT`를 container `WEB_PORT`에 매핑합니다.
- `DATABASE_PATH`: 컨테이너 내부 SQLite DB 경로입니다. 기본 Compose 기준 `data/bot.db`는 host의 `./data/bot.db`로 유지됩니다.
- `DAILY_STATS_RETENTION_DAYS`: 일별 통계 보관 기간입니다.
- `LOG_PATH`: 컨테이너 내부 애플리케이션 로그 경로입니다. 기본 Compose 기준 `logs/app.log`는 host의 `./logs/app.log`로 유지됩니다.
- `LOG_RETENTION_DAYS`: 애플리케이션 로그 보관 기간입니다.
- `SESSION_COOKIE_SECURE`: HTTPS 운영에서는 `true`, 로컬 HTTP 테스트에서는 `false`로 둡니다.
- `SESSION_COOKIE_SAMESITE`: 세션 쿠키 SameSite 값입니다. 기본값은 `Lax`입니다.
- `GOOGLE_TTS_API_KEY`: Google Cloud Text-to-Speech API Key입니다.
- `APP_ENV`: 운영에서는 `production`, 로컬 테스트에서는 `development`를 사용합니다.
- `AUDIO_QUEUE_MAXSIZE`: 서버별 음성 작업 최대 대기 개수입니다. 기본값은 25입니다.
- `AUDIO_QUEUE_MAX_PER_USER`: 사용자 한 명이 동시에 대기시킬 수 있는 최대 작업 수입니다. 기본값은 5입니다.
- `AUDIO_QUEUE_JOB_TTL_SECONDS`: 대기 작업 만료 시간입니다. 기본값은 60초입니다.
- `TTS_USER_COOLDOWN_SECONDS`: 사용자별 TTS 요청 최소 간격입니다. 기본값은 2초입니다.
- `TTS_REQUIRE_VOICE_MEMBERSHIP`: `true`이면 봇과 같은 음성 채널 사용자만 메시지를 읽을 수 있습니다.

`APP_ENV=production`에서는 OAuth 설정, 32자 이상의 `WEB_SECRET_KEY`, secure session cookie,
최소 한 명의 `DASHBOARD_ADMIN_IDS`가 없으면 애플리케이션이 시작되지 않습니다.

## 추가 봇 등록

첫 번째 봇과 대시보드가 실행된 후:

1. Discord Developer Portal에서 새 Application/Bot을 만듭니다.
2. 새 봇의 **Message Content Intent**를 켭니다.
3. 새 봇을 대상 Discord 서버에 초대합니다. scopes는 `bot`, `applications.commands`를 사용합니다.
4. 대시보드에 로그인합니다.
5. 봇 관리 섹션을 엽니다.
6. 새 봇 이름과 bot token을 입력합니다.
7. 대시보드가 Discord API로 token을 검증하고 SQLite에 저장한 뒤 worker process를 시작합니다.

추가 봇은 `bot_id` 기준으로 아래 설정이 분리됩니다.

```text
TTS 채널
키워드 치환
발음 규칙
사용량 통계
대시보드 지표
사용자 설정
```

컨테이너가 재시작되면 `enabled=1`이고 원하는 상태가 `running`인 봇만 자동으로 다시 시작합니다.
대시보드에서 Stop한 봇은 컨테이너 재시작 후에도 정지 상태를 유지합니다. 비정상 종료된 워커는
지수 backoff로 자동 복구하며, 10분 동안 5회를 초과해 실패하면 자동 재시작을 중단합니다.

## 운영 명령

재시작:

```bash
docker compose restart app
```

git 최신 코드 반영:

```bash
git pull --ff-only
docker compose build
docker compose up -d
```

로그 확인:

```bash
docker compose logs -f app
```

컨테이너 상태 확인:

```bash
docker compose ps
```

애플리케이션 상태 확인:

```bash
curl -fsS http://127.0.0.1:${WEB_PORT:-5001}/health/ready
```

기본 봇을 포함한 모든 봇은 supervisor가 독립 worker로 실행하므로 대시보드에서 동일하게
Start/Stop/Restart할 수 있습니다.

로컬 테스트:

```bash
python -m pytest tests/ -q
```

## 데이터 영속성과 백업

기본 Compose 파일은 host의 아래 디렉터리를 컨테이너에 mount합니다.

```text
./data -> /app/data
./logs -> /app/logs
```

중요한 파일은 다음입니다.

```text
./data/bot.db
```

마이그레이션이나 큰 업데이트 전에는 백업을 권장합니다.

```bash
mkdir -p data/backups
cp data/bot.db "data/backups/bot.db.backup-$(date +%Y%m%d-%H%M%S)"
```

## Troubleshooting

### `.env`가 없거나 `DISCORD_TOKEN`이 비어 있음

초기 봇 실행에는 `DISCORD_TOKEN`이 필요합니다. `.env.example`을 복사해 `.env`를 만들고 token을 입력하세요.

### 대시보드 로그인 redirect 실패

`DISCORD_REDIRECT_URI`가 Discord Developer Portal에 등록한 Redirect URI와 정확히 같은지 확인하세요. protocol, domain, port, path가 모두 일치해야 합니다.

### 로컬에서는 되는데 운영에서 로그인이 이상함

HTTPS 운영에서는 보통:

```env
SESSION_COOKIE_SECURE=true
```

로컬 HTTP 테스트에서는:

```env
SESSION_COOKIE_SECURE=false
```

를 사용합니다.

### Google TTS 실패

아래를 확인하세요.

- `GOOGLE_TTS_API_KEY`가 설정되어 있는지
- Google Cloud에서 Cloud Text-to-Speech API가 활성화되어 있는지
- API Key가 Text-to-Speech API 호출을 허용하는지
- 입력 텍스트가 5,000 UTF-8 bytes를 넘지 않는지

### `data/` 또는 `logs/` 권한 문제

Docker 컨테이너는 root가 아닌 `appuser`로 실행됩니다. mount 디렉터리에 쓰기 권한이 없으면:

```bash
mkdir -p data logs
chmod -R u+rwX,g+rwX data logs
```

필요하면 서버 환경에 맞게 ownership을 조정하세요.

## 보안 주의사항

- `.env`를 git에 commit하지 마세요.
- Bot Token이나 API Key를 issue, 로그, 스크린샷에 노출하지 마세요.
- 멀티봇 운영을 위해 bot token은 SQLite에 저장됩니다. `data/bot.db`도 secret처럼 보호하세요.
- 운영 환경에서는 HTTPS와 강한 `WEB_SECRET_KEY`를 사용하세요.

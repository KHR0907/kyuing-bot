# kyuing-bot

Discord 서버에서 [Supertonic-2](https://github.com/jnjsoftweb/supertonic) TTS 엔진을 사용해 텍스트를 음성으로 읽어주는 봇입니다. Quart 기반 웹 대시보드가 함께 실행되며, Discord OAuth 로그인과 운영 통계 확인을 지원합니다.

## 주요 기능

- 지정한 텍스트 채널의 메시지를 음성 채널에서 자동으로 읽기
- 슬래시 명령어로 개인별 TTS 설정 변경
- 서버별 TTS 채널 등록 및 해제
- 음성 채널 입장, 퇴장, 재생 중지 명령 지원
- Discord OAuth 기반 관리자 대시보드
- 일별 통계 스냅샷과 로그 파일 보관

## 슬래시 명령어

- `/join`: 현재 접속한 음성 채널로 봇을 호출
- `/leave`: 봇을 음성 채널에서 내보냄
- `/stop`: 현재 재생 중인 음성을 중지
- `/setchannel`: 현재 텍스트 채널을 TTS 채널로 등록
- `/unsetchannel`: 현재 텍스트 채널의 TTS 설정 해제
- `/channels`: 현재 서버에 등록된 TTS 채널 목록 확인
- `/voice`: 기본 음성 선택
- `/speed`: 읽기 속도 설정
- `/lang`: 기본 언어 설정
- `/quality`: 음성 품질 단계 설정
- `/settings`: 내 TTS 설정 확인
- `/voices`: 사용 가능한 음성 목록 확인

## 시스템 요구사항

- Docker & Docker Compose
- Python 3.11+ (Docker 이미지에 포함)
- FFmpeg (Docker 이미지에 포함)
- RAM: 4 GB 이상 권장 (Supertonic-2 모델이 시작 시 메모리에 로드됨)

## 빠른 시작

### 1. 환경 변수 파일 준비

`.env.example`을 복사해 `.env`를 만든 뒤 값을 채웁니다.

```bash
cp .env.example .env
```

### 2. Docker로 실행

```bash
docker compose up -d --build
```

로그 확인:

```bash
docker compose logs -f app
```

## 필수 환경 변수

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here
DISCORD_CLIENT_SECRET=your_discord_client_secret_here
DISCORD_REDIRECT_URI=https://your-domain.example/callback
DASHBOARD_ADMIN_IDS=123456789012345678,234567890123456789
WEB_SECRET_KEY=replace-with-a-long-random-secret
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
```

## 환경 변수 설명

- `DISCORD_TOKEN`: Discord 봇 토큰
- `DISCORD_CLIENT_ID`: Discord OAuth 클라이언트 ID
- `DISCORD_CLIENT_SECRET`: Discord OAuth 클라이언트 시크릿
- `DISCORD_REDIRECT_URI`: Discord 개발자 포털에 등록한 OAuth 콜백 URL
- `DASHBOARD_ADMIN_IDS`: 기본 관리자 Discord 사용자 ID 목록, 쉼표로 구분
- `WEB_SECRET_KEY`: 세션 서명용 비밀 키
- `WEB_PORT`: 웹 대시보드 포트
- `DATABASE_PATH`: SQLite 데이터베이스 파일 경로
- `DAILY_STATS_RETENTION_DAYS`: 일별 통계 보관 기간
- `LOG_PATH`: 애플리케이션 로그 파일 경로
- `LOG_RETENTION_DAYS`: 로그 보관 기간
- `SESSION_COOKIE_SECURE`: HTTPS 환경에서 `true` 권장
- `SESSION_COOKIE_SAMESITE`: 세션 쿠키 SameSite 설정

## 서버 배포

### 1. Docker 설치

Ubuntu 예시:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

### 2. 애플리케이션 배포

```bash
git clone <repo-url>
cd kyuing-bot
cp .env.example .env
mkdir -p data
mkdir -p logs
docker compose up -d --build
```

## 운영 명령

재시작:

```bash
docker compose restart app
```

업데이트:

```bash
git pull
docker compose up -d --build
```

로그 확인:

```bash
docker compose logs -f app
```

애플리케이션 로그는 `logs/app.log`에 저장되며 기본 30일 동안 유지됩니다. 대시보드 일별 통계는 기본 365일 동안 유지됩니다.

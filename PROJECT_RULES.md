# Project Rules

이 문서는 현재 프로젝트에서 대화로 확정된 운영/개발 규칙을 정리한 것이다.

## Runtime

- 웹/봇 서비스 기본 포트는 `5001`을 사용한다.
- 앱은 Docker로 배포한다.
- 운영 재배포 기본 명령은 아래를 사용한다.

```bash
git pull origin main
sudo docker compose up -d --build
sudo docker compose logs -f app
```

- 일반 빌드에서는 `--no-cache`를 사용하지 않는다.
- Docker 빌드 캐시 최적화는 `Dockerfile`에 반영되어 있다.

## Domain And Network

- 운영 도메인은 공개 서비스 도메인을 사용한다.
- Discord OAuth Redirect URL은 아래 값을 사용한다.

```text
https://your-domain.example/callback
```

- Nginx는 외부 `80/443`을 받고 내부 앱 `127.0.0.1:5001`로 프록시한다.
- CDN 또는 프록시를 사용할 경우 HTTPS 종단 구성과 원본 인증서를 함께 관리한다.
- `.dev` 도메인이므로 HTTPS 전제를 유지한다.
- 서버/클라우드 방화벽에서는 `80/tcp`, `443/tcp` 인바운드가 열려 있어야 한다.
- 서버 로컬 방화벽에서도 `80`, `443` 허용이 필요하다.

## Environment Variables

- `.env.example`와 `.env`는 같은 키 구조를 유지한다.
- `.env`에 실제 시크릿을 넣고, `.env.example`에는 placeholder만 둔다.
- 주요 env 규칙:

```env
DISCORD_REDIRECT_URI=https://your-domain.example/callback
WEB_PORT=5001
DATABASE_PATH=data/bot.db
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
```

- 대시보드 슈퍼 어드민은 아래 env로 관리한다.

```env
DASHBOARD_ADMIN_IDS=111111111111111111,222222222222222222
```

- `DASHBOARD_ADMIN_IDS`에는 Discord 사용자 ID만 넣는다.

## Dashboard Admin Policy

- 봇 owner는 대시보드 접근 가능하다.
- `DASHBOARD_ADMIN_IDS`에 있는 사용자는 슈퍼 어드민이다.
- 슈퍼 어드민은 대시보드 관리자 관리 화면에서 삭제할 수 없다.
- 대시보드에서 직접 추가한 관리자만 대시보드에서 삭제할 수 있다.
- 관리자 목록에서 사용자 ID는 기본적으로 숨긴다.
- 슈퍼 어드민으로 로그인한 경우에만 이름 옆 작은 `ID` 버튼으로 사용자 ID를 볼 수 있다.

## Discord Bot Behavior

- TTS 채널 추가는 디스코드 슬래시 명령 `/setchannel`로 처리한다.
- TTS 채널 제거는 `/unsetchannel`을 사용한다.
- TTS 채널 목록 확인은 `/channels`를 사용한다.
- 음성 참가/퇴장은 `/join`, `/leave`, `/stop`을 사용한다.
- 슬래시 명령 실행 실패는 서버 로그에 남긴다.
- `/join` 등 음성 명령은 defer 처리와 예외 로그를 남긴다.
- 봇이 들어가 있는 음성 채널에 사람(봇 제외)이 없으면 자동 퇴장한다.

## Logging And Retention

- 앱 파일 로그는 `logs/app.log`를 사용한다.
- 앱 파일 로그 보관 기간은 `30일`이다.
- 대시보드 일별 통계 보관 기간은 `365일`이다.
- 운영 확인용 로그:
  - 앱 컨테이너 로그: `sudo docker compose logs -f app`
  - 앱 파일 로그: `tail -f logs/app.log`
  - Nginx access log: `sudo tail -f /var/log/nginx/access.log`
  - Nginx error log: `sudo tail -f /var/log/nginx/error.log`

## Docker And Files

- `data/`는 DB 저장용 볼륨이다.
- `logs/`는 앱 파일 로그 저장용 볼륨이다.
- `logs/`, `data/`, `*.db`는 Git에 포함하지 않는다.
- `.claude/`는 Git에 포함하지 않는다.

## Security

- Discord 사용자 ID는 민감도 낮은 식별자지만, 대시보드에는 기본 노출하지 않는다.
- 아래 값은 유출 즉시 교체한다:
  - `DISCORD_TOKEN`
  - `DISCORD_CLIENT_SECRET`
  - OAuth access token / refresh token
  - 세션 쿠키

## Local Development

- 로컬 웹 주소는 기본적으로 `http://localhost:5001`이다.
- 로컬 OAuth 테스트 시 아래 값을 사용한다.

```env
DISCORD_REDIRECT_URI=http://localhost:5001/callback
SESSION_COOKIE_SECURE=false
```

- 로컬 음성 기능을 위해 시스템 `ffmpeg`가 필요하다.

## Voice Dependency

- 현재 음성 기능을 위해 `discord.py`, `PyNaCl`, `davey`를 사용한다.
- `requirements.txt`에 `davey`를 포함한다.

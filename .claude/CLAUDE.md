# Project Guidelines

## Project Structure
- `bot.py` — 엔트리포인트 (봇 + 웹서버 동시 기동)
- `config.py` — 환경변수, 상수 정의
- `database.py` — SQLite(aiosqlite) 데이터 계층
- `tts_engine.py` — Supertonic TTS 엔진 래퍼
- `cogs/` — discord.py Cog 패턴 슬래시 명령어 모듈
- `web/` — Quart 웹 대시보드 (OAuth2 + 설정 관리)

## Environment Variables
- `.env` 파일에 새로운 환경변수를 추가하거나 기존 변수를 수정/삭제할 때, 반드시 `.env.example`도 함께 업데이트할 것
- `.env.example`에는 실제 값 대신 placeholder를 사용 (예: `your_discord_bot_token_here`)

## Conventions
- loguru 사용: `from loguru import logger as log`
- 데이터 저장은 SQLite(`database.py`)를 통해 — JSON 파일이나 메모리 딕셔너리 사용 금지
- 새 슬래시 명령어는 `cogs/` 디렉터리에 Cog 패턴으로 추가

# 사운드보드 기능 설계 (Soundboard)

- 날짜: 2026-06-11
- 상태: 승인됨

## 개요

8초 이하의 짧은 음원(mp4 등 주요 오디오/비디오 형식)을 키워드와 함께 등록하고,
`/play <키워드>` 슬래시 명령어로 음성 채널에서 재생하는 기능.

## 요구사항

- 재생은 `/play <키워드>` 슬래시 명령어로만 트리거된다. 일반 메시지는 기존 TTS 동작 그대로.
- 등록 경로는 두 가지: ① 슬래시 명령어 + 파일 첨부, ② 웹 대시보드 업로드.
- 음원 범위는 2계층: 전역(봇 전체) + 길드별. 키워드 해석 우선순위는 길드 → 전역.
- 권한: 길드 음원은 길드 멤버 누구나 등록/삭제 가능, 전역 음원은 대시보드 관리자만(대시보드에서만) 관리.
- 허용 형식: ffmpeg이 읽을 수 있는 주요 오디오/비디오 형식 (mp4, mp3, wav, ogg, webm, m4a 등).
- 길이 제한: 8초 이하. 초과 시 등록 거부.
- 파일 크기 제한: 20MB. 길드당 음원 최대 100개.
- 멀티봇: 모든 데이터는 기존 구조대로 `bot_id` 스코프로 분리.

## 데이터 모델

`database.py`의 멀티봇 테이블 생성부(`_create_multibot_tables`)에 추가:

```sql
CREATE TABLE IF NOT EXISTS sounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('global', 'guild')),
    guild_id INTEGER,                  -- 전역이면 NULL
    keyword TEXT NOT NULL,
    filename TEXT NOT NULL,            -- data/sounds/<bot_id>/ 아래 파일명
    duration_seconds REAL NOT NULL,
    original_filename TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    play_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(bot_id, scope, guild_id, keyword)
);
```

- 같은 범위(scope/guild) 내 키워드 중복 등록은 에러. 변경하려면 삭제 후 재등록.
- 키워드 최대 길이 50자.
- `play_count`는 재생 시마다 증가시켜 대시보드 통계에 사용.

## 오디오 처리 파이프라인 — 새 모듈 `sound_storage.py`

슬래시 명령어와 대시보드가 공유하는 업로드 공통 로직:

1. 파일 수신 (최대 20MB), 임시 파일로 저장.
2. `ffprobe`로 검사: 오디오 스트림 존재 + 길이 8초 이하. 위반 시 거부.
3. `ffmpeg -i <in> -vn -ac 2 -ar 48000 -c:a libopus <out>.ogg`로 오디오만 추출·변환.
4. `data/sounds/<bot_id>/<uuid>.ogg`로 저장하고 DB에 메타데이터 기록.
5. 삭제 시 DB 행과 디스크 파일을 함께 제거.

ffprobe/ffmpeg 호출은 `asyncio.create_subprocess_exec`로 비동기 실행.
Docker 이미지에 ffmpeg이 이미 포함되어 있어 추가 의존성 없음.

## 슬래시 명령어 — 새 cog `cogs/sounds.py`

| 명령어 | 동작 | 권한 |
|---|---|---|
| `/sound add <키워드> <파일첨부>` | 현재 길드에 음원 등록 | 누구나 |
| `/sound remove <키워드>` | 길드 음원 삭제 | 누구나 |
| `/sound list` | 사용 가능한 음원 목록 (길드 + 전역) | 누구나 |
| `/play <키워드>` | 음원 재생 (키워드 자동완성) | 누구나 |

- `/play`는 호출자의 현재 음성 채널(없으면 봇이 접속해 있는 채널)에서 재생.
- 재생은 `tts_engine.py`의 길드별 락(`_locks`)을 공유하여 TTS와 음원이 서로
  끊지 않고 순서대로 재생되게 한다.
- 음성 클라이언트 연결/이동/stale 재연결 로직은 `tts_engine.do_tts`의 기존 코드를
  헬퍼 함수로 추출해 재사용한다.
- `bot.py`의 `EXTENSIONS`에 `cogs.sounds` 추가.

## 대시보드

- 음원 관리 섹션 추가: 전역/길드별 음원 목록(키워드, 길이, 등록자, 재생 횟수,
  등록일), 업로드 폼(파일 + 키워드 + 범위/길드 선택), 삭제 버튼.
- 전역 음원은 대시보드에서만 등록/삭제 가능 (기존 관리자 로그인 게이트 그대로).
- 라우트: `POST /sounds/upload`, `POST /sounds/<id>/delete`.

## 에러 처리

- 등록 시: 8초 초과 / 오디오 트랙 없음 / 변환 실패 / 20MB 초과 / 길드당 100개 초과 /
  키워드 중복 → 거부 + 사유 안내.
- `/play` 시:
  - 음성 채널 미접속 → "먼저 음성 채널에 접속해주세요!"
  - 미등록 키워드 → 안내 메시지.
  - DB에는 있으나 디스크에 파일 없음 → 에러 안내 후 해당 DB 행 정리.

## 테스트

- `tests/test_sounds.py` (기존 `tests/test_database_multibot.py` 스타일):
  - sounds CRUD + bot_id 스코프 분리.
  - 키워드 해석 우선순위 (길드 > 전역).
  - 길이/형식 검증 로직 (ffprobe 출력 모킹).
  - 길드당 100개 제한, 키워드 중복 거부.

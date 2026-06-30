# Lavalink 기반 음악 재생 기능 설계

작성일: 2026-06-30

## 목표

뀨잉봇에 YouTube 음원 재생 기능을 추가한다. Lavalink를 별도 컨테이너로 띄우고
Wavelink로 연결하여, 봇 본체 부하를 늘리지 않으면서 안정적으로 음악을 스트리밍한다.
기존 TTS/사운드보드 기능과 같은 음성 연결을 공유하되, TTS가 발생하면 음악을 잠시
일시정지했다가 재개하는 "인터럽트 덕킹"으로 공존시킨다.

## 확정된 결정사항

- **TTS 우선 (인터럽트 덕킹)** — 음악 재생 중 TTS 메시지가 오면 음악을 일시정지하고
  TTS를 재생한 뒤 음악을 재개한다. 진짜 볼륨 덕킹(믹싱)은 추후 개선 과제.
- **YouTube만 지원** — Lavalink youtube-source 플러그인.
- **풀 기능 명령어 세트** — play/skip/stop/pause/resume/queue/nowplaying/volume/
  remove/clear/loop/shuffle/seek/speed.
- **Lavalink는 docker-compose 서비스로 추가** — 모든 봇/워커가 노드 1개를 공유.
- **Wavelink** 클라이언트 라이브러리 (discord.py 전용, v3 = Lavalink v4 필요).
- **연결 핸드오프 방식**의 인터럽트 덕킹 (아래 상세).
- **`/help` 명령어 추가** — 전체 명령어 사용법 가이드.
- **빈 채널 자동 퇴장은 기존 로직(즉시 퇴장) 재사용** — 신규 타이머 없음.

## 섹션 1 — 아키텍처 & 인프라

```
docker-compose.yml
├─ app (기존: 봇 + 웹 + 워커)
│    └─ Wavelink Pool ──WebSocket──┐
└─ lavalink (신규)                  │
     image: ghcr.io/lavalink-devs/lavalink:4-alpine
     + youtube-source 플러그인       ◄┘
     port 2333 (내부 네트워크), application.yml 마운트
     _JAVA_OPTIONS=-Xmx512M
```

### 신규/변경 파일

- `docker-compose.yml` — `lavalink` 서비스 추가. `app`은 `depends_on: lavalink`,
  같은 네트워크에 배치. 메모리 `-Xmx512M`.
- `application.yml` (신규, 프로젝트 루트) — Lavalink 서버 설정 + youtube-source
  플러그인 선언. compose에서 컨테이너로 마운트.
- `config.py` — 다음 추가:
  - `LAVALINK_HOST` (기본 `lavalink`)
  - `LAVALINK_PORT` (기본 `2333`)
  - `LAVALINK_PASSWORD` (기본 `youshallnotpass`, 운영은 env로 교체)
  - `MUSIC_ENABLED` (기본 `true`, 런타임 토글용)
- `.env.example` — 위 변수 placeholder 추가 (CLAUDE.md 규칙 준수).
- `requirements.txt` — `wavelink>=3` 추가.

### 멀티봇 동작

각 워커 봇 프로세스가 각자 Wavelink Pool로 **같은 Lavalink 노드**에 연결한다.
Lavalink는 여러 클라이언트를 동시에 받으므로 노드 1개를 공유한다. 봇별 Lavalink
분리는 하지 않는다(YAGNI).

### Lavalink 미연결 / 비활성 시

`MUSIC_ENABLED=false`이거나 노드 연결에 실패하면 음악 명령어는
"🎵 음악 기능을 일시적으로 사용할 수 없어요"로 응답하고, **TTS/사운드보드/웹은
영향 없이 기존대로** 동작한다. 봇은 죽지 않는다.

## 섹션 2 — 음악 Cog & 명령어

### 신규 파일: `cogs/music.py`

사운드보드 cog 패턴을 따른다. 모든 음악 명령어는 충돌 회피를 위해 **`/music` 그룹
하위**로 묶는다 (기존 최상위 `/play`(사운드), `/stop`(TTS), `/speed`(TTS),
`/sound remove`와 분리됨). Discord 슬래시 그룹은 2단계까지만 허용되므로 이 구조가
한계 안에서 최적이다.

| 명령어 | 동작 |
|---|---|
| `/music play <검색어\|URL>` | 트랙 검색 → 큐 추가, 안 틀고 있으면 즉시 재생 |
| `/music skip` | 현재 곡 스킵 |
| `/music stop` | 정지 + 큐 비우기 + 음성 퇴장 |
| `/music pause` | 일시정지 |
| `/music resume` | 재개 |
| `/music queue` | 큐 목록 (현재곡 + 대기열, 페이지네이션) |
| `/music nowplaying` | 현재곡 정보 (제목/작성자/진행시간/썸네일) |
| `/music volume <0~100>` | 볼륨 조절 |
| `/music remove <번호>` | 큐에서 특정 곡 제거 |
| `/music clear` | 큐 전체 비우기 (현재곡 유지) |
| `/music loop <off\|track\|queue>` | 반복 모드 |
| `/music shuffle` | 큐 셔플 |
| `/music seek <초>` | 재생 위치 이동 |
| `/music speed <0.5~2.0>` | 배속 (timescale 필터) |

### Wavelink 이벤트 핸들러

- `on_wavelink_node_ready` — 노드 연결 로깅.
- `on_wavelink_track_start` — "Now Playing" 임베드를 명령 채널(`player.home`)에 전송.
- `on_wavelink_track_end` — loop 모드에 따라 다음 곡 진행 / 정지.

### 자동재생 / 배속

- 자동재생: `player.autoplay = AutoPlayMode.partial` (큐 소진 시 정지, 추천곡 fetch
  안 함 — 의도치 않은 무한재생 방지). 반복은 `/music loop queue`로 별도 제어.
- 배속: `/music speed` → `player.filters`의 `timescale.set(speed=...)`.

### 공통 가드

- 길드 밖이면 거부.
- 사용자 음성 미접속 + 봇도 미접속 → "먼저 음성 채널에 들어와주세요".
- `MUSIC_ENABLED` / 노드 연결 확인.

### 신규 파일: `cogs/help.py` — `/help [category]`

뀨잉봇 전체 명령어 사용법 가이드. 음악과 무관한 범용 기능이므로 별도 cog로 둔다
(음악 기능을 꺼도 `/help`는 동작).

- `/help` (인자 없음) → 카테고리 개요 + 각 카테고리 요약.
- `/help <category>` → 해당 카테고리 명령어 상세 (자동완성으로 카테고리 선택).
- 음악 카테고리는 `MUSIC_ENABLED`일 때만 노출.
- ephemeral 응답 (채널 도배 방지).

카테고리:

| 카테고리 | 포함 명령어 |
|---|---|
| 🎙️ 음성/채널 | `/join` `/leave` `/stop` `/setchannel` `/unsetchannel` `/channels` |
| ⚙️ TTS 설정 | `/engine` `/voice` `/speed` `/lang` `/quality` `/settings` `/voices` `/pronounce` `/usage` |
| 🔊 사운드보드 | `/sound add\|remove\|list` `/play` |
| 🎵 음악 | `/music play\|skip\|stop\|pause\|resume\|queue\|nowplaying\|volume\|remove\|clear\|loop\|shuffle\|seek\|speed` |

가능하면 봇에 등록된 app_commands를 순회해 자동 생성하되, 카테고리 분류·한글 설명을
위한 메타데이터(카테고리 매핑)는 둔다. 구체 방식은 구현 계획에서 결정.

## 섹션 3 — 데이터 흐름 & TTS 인터럽트 덕킹 (연결 핸드오프)

### 음악 재생 흐름

```
/music play <쿼리>
  → wavelink.Playable.search(쿼리)       (YouTube)
  → player.queue.put_wait(track)
  → 안 틀고 있으면 player.play(queue.get())
  → on_wavelink_track_start: "Now Playing" 임베드
  → on_wavelink_track_end: loop 모드 따라 다음 곡 / 정지
```

### 핵심 제약: 음성 연결 소유권

Wavelink Player가 음악을 틀면 `guild.voice_client`가 `wavelink.Player` 인스턴스가
된다. 기존 TTS/사운드는 `vc.play(FFmpegPCMAudio(...))`를 호출하는데 `wavelink.Player`
에는 그 메서드가 없다. Discord는 길드당 음성 연결을 1개만 허용하므로, 음악 중
TTS를 기본 재생하려면 **연결을 잠시 핸드오프**해야 한다.

### 상태 관리 (휘발성)

- 핸드오프 중 곡 위치/트랙은 **`do_tts`/`play_sound`의 지역 변수로만** 보관한다.
  DB나 인스턴스 속성에 저장하지 않는다.
- 재개에 사용한 직후 변수는 스코프 종료로 자연 소멸 → 어디에도 잔존하지 않는다.
- 전 과정을 길드 락(`_locks[guild.id]`) 안에서 수행해, 핸드오프 도중 다른
  TTS/사운드가 끼어들지 못하게 보장한다.

### 핸드오프 시퀀스 (음악 재생 중 TTS 도착)

```python
async with _locks[guild.id]:
    # 합성은 음악을 건드리기 전에 수행 — 합성 실패 시 음악 무사
    tmp_path = await engine.synthesize(...)

    player = guild.voice_client   # wavelink.Player 일 수 있음
    is_music_active = isinstance(player, wavelink.Player) and player.playing

    if is_music_active:
        resume_track = player.current
        resume_ms = max(0, player.position - 200)   # ← 에어백 0.2초, 0 미만 clamp
        await player.pause(True)
        await player.disconnect()                   # Wavelink 연결 해제
        # 이 시점부터 resume_ms / resume_track 만이 휘발성 상태

    # --- 기존 TTS/사운드 재생 경로 그대로 ---
    vc = await voice_channel.connect()              # 기본 VoiceClient
    vc.play(discord.FFmpegPCMAudio(tmp_path))
    while vc.is_playing():
        await asyncio.sleep(0.5)
    await vc.disconnect()

    if is_music_active:
        player = await voice_channel.connect(cls=wavelink.Player)
        await player.play(resume_track, start=resume_ms)  # 저장값-0.2s 부터 재개
        # resume_track / resume_ms 는 여기서 마지막 사용, 블록 종료로 휘발
```

### 엣지 케이스

- 재개 위치가 곡 길이 근처면 재개 직후 track_end 발생 → 큐 다음 곡으로 정상 진행.
- 핸드오프 중 예외 → `finally`에서 임시파일 정리 + `resume_track`이 있으면 음악
  재연결 시도. 재연결 실패 시 로그만 남기고 조용히 포기 (TTS는 best-effort).
- `resume_track`이 라이브 스트림 등 seek 불가면 `start` 무시하고 처음부터.
- 핸드오프 도중 사용자가 채널을 떠나 대상 채널이 비면 재연결 생략.
- 사운드보드(`play_sound`)도 동일 로직 공유 (이미 같은 락 사용).

### 리팩터링 포인트

핸드오프 로직은 신규 파일 **`music_handoff.py`** 로 분리하고, `tts_engine.py`는
`is_music_active()` / `play_with_handoff()` 같은 함수만 호출한다. Wavelink import는
옵셔널 처리하여, 음악 비활성/미설치 시 즉시 False를 반환하고 기존 경로를 탄다.
음악을 한 번도 안 튼 길드의 TTS/사운드는 코드 경로가 완전히 동일하다(영향 0).

## 섹션 4 — 에러 처리 & 엣지 케이스

### Lavalink 연결

| 상황 | 처리 |
|---|---|
| 봇 시작 시 Lavalink 미연결 | 음악 명령은 "일시적으로 사용할 수 없어요". TTS/사운드/웹 정상. 봇 안 죽음 |
| 노드 중간 끊김 | `on_wavelink_node_*` 이벤트로 재연결 시도 (Wavelink 내장 resume). 재생 중 곡은 복구 시 best-effort |
| `MUSIC_ENABLED=false` | 명령어는 등록하되 "비활성화됨" 응답 (런타임 토글 용이) |

### 음악 명령어 가드 (모든 `/music *` 공통)

- 길드 밖 → 거부.
- 사용자 음성 미접속 + 봇 미접속 → "먼저 음성 채널에 들어와주세요".
- 검색 결과 없음 → "검색 결과가 없어요".
- 큐 조작인데 재생 중 아님 → "재생 중인 곡이 없어요".
- `/music remove` 범위 밖 → "그 번호의 곡이 없어요".
- `volume`/`seek`/`speed` 범위 밖 → 범위 안내 후 거부.

### 핸드오프 실패 격리

- TTS 합성은 음악을 건드리기 전에 수행 → 합성 실패 시 음악 무사.
- 기본 VoiceClient 재생 중 예외 → `finally` 정리 + `resume_track` 있으면 재연결 시도.
- 음악 재연결 실패 → 로그만 남기고 포기, 사용자에겐 TTS 결과만. 봇은 안정.

### 빈 채널 자동 퇴장 (기존 로직 재사용)

신규 타이머를 만들지 않는다. 기존 `bot.py`의 `disconnect_if_voice_channel_empty`
(사람 0명이면 즉시 `vc.disconnect()`)를 그대로 사용한다. `vc.disconnect()`는
`wavelink.Player`(VoiceProtocol)에도 동작하므로 음악 Player도 함께 끊긴다. 추가로
**그 길드의 음악 큐 정리만 보강**한다(Wavelink Player였으면 큐 비우기 한 줄).

### 자원/누수 방지

- 봇 종료/재시작 시 Wavelink Pool 정리 (`bot.close` 훅).
- 멀티봇: 워커 종료 시 해당 봇의 Player만 정리 (다른 봇 영향 없음).

## 섹션 5 — 테스트 전략

Lavalink·Discord 음성은 외부 의존이라 통합 테스트가 어렵다. 순수 로직을 외부
의존에서 분리해 단위 테스트(pytest, mock)하는 데 집중한다.

### 단위 테스트 (mock)

- `music_handoff.py`:
  - 음악 비활성 → 핸드오프 없이 기존 경로.
  - 음악 활성 → pause → disconnect → 재생 → reconnect 순서 검증.
  - `resume_ms = max(0, position - 200)` 에어백 계산. `position=100` → `resume_ms=0`.
  - 핸드오프 중 예외 → finally 정리 호출.
  - `resume_track`/`resume_ms`가 사용 후 참조되지 않음(휘발) 검증.
- `/help` 카테고리 매핑이 등록된 명령어와 일치하는지 (하드코딩 누락 방지).
- config 파싱: `LAVALINK_*`, `MUSIC_ENABLED` 기본값/플래그.
- 명령어 이름 충돌 없음: 기존 + `/music *` 전체 이름 유일성.

### mock 경계로 격리

- Wavelink `Pool.connect`, 실제 트랙 검색/재생은 mock으로 대체. 우리 로직만 검증.
- 실제 오디오 송출은 테스트하지 않음 (수동 검증 항목).

### 수동 검증 체크리스트

1. `/music play`로 YouTube 곡 재생.
2. 재생 중 TTS 채널에 메시지 → 음악 멈췄다 TTS 후 **0.2초 앞에서** 재개.
3. `/music skip/pause/resume/queue/volume/loop/shuffle/seek/speed` 동작.
4. 빈 채널 → 즉시 퇴장 + 큐 정리.
5. Lavalink 끄고 봇 시작 → 음악 명령 거부, TTS 정상.
6. `/help` 및 `/help <category>` 표시 확인.

## 구현 마일스톤 (개략)

풀 기능 + 인프라라 범위가 크므로 구현 계획에서 다음 순서로 나눈다:

1. 인프라 (compose + application.yml + config + requirements + Wavelink Pool 연결).
2. `cogs/music.py` 핵심 (play/skip/stop/queue/nowplaying) + 이벤트 핸들러.
3. 나머지 명령어 (pause/resume/volume/remove/clear/loop/shuffle/seek/speed).
4. `music_handoff.py` + `tts_engine.py` 연동 (인터럽트 덕킹).
5. 빈 채널 퇴장 시 큐 정리 보강.
6. `cogs/help.py`.
7. 테스트 + 문서(README) 업데이트.

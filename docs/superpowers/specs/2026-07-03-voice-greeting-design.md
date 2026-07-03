# 음성 채널 입/퇴장 TTS 알림 설계 (Voice Greeting)

- 날짜: 2026-07-03
- 상태: 승인됨

## 개요

봇이 접속해 있는 음성 채널에 유저가 입장하거나 퇴장하면, 그 사실을 TTS로
읽어주는 기능. 기본 문구를 제공하되 유저별로 자신의 입장/퇴장 멘트를
커스텀할 수 있다.

## 요구사항

- 이벤트 범위: 입장(join)과 퇴장(leave)만. 같은 서버 내 채널 이동(move)은 알리지 않는다.
- 감지 범위: 봇이 현재 접속해 있는 음성 채널만. 봇이 없는 채널은 감지하지 않는다.
- 문구: 기본 문구("OO님이 입장했어요" / "OO님이 퇴장했어요") 제공 + 유저별 커스텀 멘트.
- on/off: 서버별 토글, 기본 **OFF**. `manage_guild` 권한자가 `/greeting on|off`로 제어.
- 봇 계정(자신 포함)의 입/퇴장은 무시한다 (`member.bot`).
- 멀티봇: 모든 데이터는 기존 구조대로 `bot_id` 스코프로 분리.

## 데이터 모델

`database.py`에 추가:

```sql
CREATE TABLE IF NOT EXISTS guild_greeting_settings (
    bot_id INTEGER NOT NULL DEFAULT 0,
    guild_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, guild_id)
);
```

`user_settings` 테이블에 컬럼 추가 (마이그레이션: `ALTER TABLE ... ADD COLUMN`,
기존 컬럼 추가 패턴 준수):

- `join_message TEXT` — NULL이면 기본 문구
- `leave_message TEXT` — NULL이면 기본 문구

신규 database 함수:

- `is_greeting_enabled(guild_id, bot_id) -> bool` — 핫패스이므로
  `get_tts_channels_cached`와 동일한 캐시 패턴 적용.
- `set_greeting_enabled(guild_id, enabled, bot_id)` — 캐시 무효화 포함.
- 유저 멘트는 기존 `set_user_setting(user_id, join_message=..., leave_message=...)` 재사용.

## 모듈 구조

로직 비대화를 막기 위해 판별/문구 로직은 별도 모듈로 분리한다.

| 파일 | 역할 |
|---|---|
| `greeting.py` (신규) | 이벤트 판별 + 문구 생성 순수 로직 |
| `cogs/greetings.py` (신규) | `/greeting` 슬래시 명령 그룹 |
| `database.py` | 테이블 + enabled 함수 + 캐시 |
| `bot.py` | `on_voice_state_update`에 훅 연결만 |
| `cogs/help.py` | "음성/채널" 카테고리에 greeting 추가 |

### greeting.py 공개 인터페이스

- `resolve_event(member, before, after, watched_channel_id) -> str | None`
  - 순수 함수. `"join"` / `"leave"` / `None`(알림 대상 아님) 반환.
  - `member.bot`이면 None.
  - before/after 모두 음성 채널인 상태에서 감시 채널을 드나드는 경우는
    move로 보고 None. (감시 채널 → 타 채널, 타 채널 → 감시 채널 모두 move)
  - after만 감시 채널이면 join (before는 None), before만 감시 채널이면 leave (after는 None).
- `build_greeting_text(event, display_name, custom_message) -> str`
  - 커스텀 멘트가 있으면 그것을 사용, `{name}` 플레이스홀더는 서버
    닉네임(`member.display_name`)으로 치환. 플레이스홀더가 없으면 멘트 그대로.
  - 커스텀 멘트가 없으면 기본 문구: `"{name}님이 입장했어요"` / `"{name}님이 퇴장했어요"`.

## 감지 → 재생 흐름

```
on_voice_state_update (bot.py)
  1. voice_client 없으면 return (기존)
  2. event = greeting.resolve_event(member, before, after, watched_channel_id)
  3. event가 leave이고 채널에 남은 사람이 0명이면 멘트 생략
     → disconnect_if_voice_channel_empty (기존 로직)
  4. event 있고 is_greeting_enabled(guild) 이면:
     - 유저 커스텀 멘트 로드 (get_user_settings)
     - build_greeting_text로 문구 생성
     - apply_keyword_replacement 적용 (발음 규칙 일관성)
     - do_tts로 재생. 보이스는 입/퇴장한 유저의 TTS 설정을 사용한다
       (그 유저의 메시지를 읽을 때와 동일한 목소리 — 구현 단순화 + 일관성)
  5. 빈 채널 disconnect 체크 (기존)
```

기존 `on_voice_state_update`의 disconnect 로직은 이 훅과 함께 자연스럽게
읽히도록 리팩토링해도 된다 (사용자 승인됨).

## 슬래시 명령 — `/greeting` 그룹

| 명령어 | 권한 | 기능 |
|---|---|---|
| `/greeting on` | manage_guild | 서버 알림 켜기 |
| `/greeting off` | manage_guild | 서버 알림 끄기 |
| `/greeting status` | 누구나 | 서버 on/off + 내 커스텀 멘트 표시 (ephemeral) |
| `/greeting join <문구>` | 누구나 | 내 입장 멘트 설정. 빈 문자열이면 기본 문구로 초기화 |
| `/greeting leave <문구>` | 누구나 | 내 퇴장 멘트 설정. 빈 문자열이면 기본 문구로 초기화 |

권한 데코레이터는 `cogs/channels.py` 패턴을 따른다.

## 안전장치 / 엣지 케이스

- 커스텀 멘트 최대 **100자**. 초과 시 설정 거부 (ephemeral 오류 메시지).
- **입퇴장 도배 방지**: (guild_id, user_id)별 **5초 쿨다운**. 메모리 dict로
  관리하며 재시작 시 초기화되어도 무방.
- 퇴장 이벤트에서 채널에 남은 사람이 0명이면 멘트를 읽지 않고 바로 disconnect
  (들을 사람이 없음).
- TTS 재생 중 알림 발생 시 기존 do_tts 큐잉 방식 그대로 사용 (별도 처리 없음).

## 테스트

- `tests/test_greeting.py` — `resolve_event`(join/leave/move/bot/타 채널),
  `build_greeting_text`(기본/커스텀/{name} 치환).
- `tests/test_greetings_cog.py` — 기존 cog 테스트 패턴(stub 스냅샷/복원)으로
  명령 존재 + 그룹 구조 확인.
- database — enabled 토글, 캐시 무효화, user_settings 컬럼 마이그레이션.

# LGTM Worktime Slack Bot

Slack에서 출퇴근, 휴식, 시간대별 업무 로그, 월간 작업 잔디, 일일 스크럼을 관리하는 팀용 봇입니다. 기존 Discord 버전의 근태/스크럼 흐름을 Slack Native UX로 옮겼습니다.

## 주요 기능

- `/worktime 출근` — 현재 시각으로 근무 시작
- `/worktime 휴식` — 휴식 시작/종료 토글
- `/worktime 퇴근` — 퇴근 처리 및 `퇴근 - 출근 - 총 휴식` 기준 실작업시간 계산
- `/worktime 기록 [@사용자] [YYYY-MM-DD]` — 일별 출퇴근/휴식/실작업 조회
- `/worktime 잔디 [@사용자] [YYYY-MM]` — 월간 작업시간을 GitHub 스타일 잔디로 표시
- `/setlog 내용` — 현재 한 시간 구간의 업무 내용을 저장/수정
- `/today [@사용자]` — 오늘 시간대별 업무 로그와 실작업시간 조회
- `/scrum` — 오늘의 일일 업무보고를 Modal로 작성하고 검토 후 스크럼 채널에 공개
- 매 정각 현재 출근 상태인 사용자에게 `/setlog` 입력 알림

기본 시간대는 `Asia/Seoul`이며 PostgreSQL을 사용하므로 Railway 재배포/재시작 후에도 기록이 유지됩니다.

## Slack UX

Slack의 Slash Command 이름은 영문 명령으로 등록하고, 실제 동작은 한글 하위 명령으로 제공합니다.

```text
/worktime 출근
/worktime 휴식
/worktime 퇴근
/worktime 기록 @김서연 2026-09-13
/worktime 잔디 @김서연 2026-09
/setlog K8s scanner rule validation 진행
/today @김서연
/scrum
```

`/worktime`만 입력하면 도움말이 표시됩니다. `in`, `break`, `out`, `record`, `grass` 영문 별칭도 지원합니다.

## 근태 규칙

- 같은 워크스페이스/사용자/날짜에는 하나의 근무 세션만 생성합니다.
- 열린 근무 세션이 있으면 다음 출근을 차단합니다.
- 휴식 중 퇴근하면 진행 중 휴식을 자동 종료하고 총 휴식시간에 합산합니다.
- 자정을 넘어 퇴근하더라도 출근한 날짜의 근무 기록으로 유지됩니다.
- `/setlog`는 현재 시각이 속한 `HH:00~HH+1:00` 구간에 저장되며 같은 구간에 재입력하면 수정됩니다.

## 일일 스크럼

`/scrum`을 실행하면 작성자에게 Slack Modal이 열립니다.

1. 담당 모듈 / 작업 영역
2. 완료한 일
3. 진행 중인 일
4. 다음에 할 일
5. 어려웠던 점 / 비고

Modal 제출은 초안 저장입니다. 초안은 작성자에게만 Ephemeral 메시지로 보이며, `완성 및 공유` 버튼을 눌러야 `SCRUM_CHANNEL_ID`에 공개됩니다. 완성 후에는 수정할 수 없습니다.

외부 공유 전 다음 문자열을 차단합니다.

- `@channel`, `@here`, `@everyone` 류 전체 멘션
- AWS Access Key
- Slack/GitHub 토큰
- Slack Incoming Webhook URL
- Private Key 헤더
- `password=...`, `token=...`, `secret=...`, `api_key=...` 류 값

사용자 입력에 포함된 `<...>` Slack 특수 토큰은 escape하여 실행되지 않도록 처리합니다.

## Slack App 생성

가장 빠른 방법은 이 저장소의 [`manifest.yaml`](./manifest.yaml)을 사용하는 것입니다.

1. Slack API의 **Create New App → From an app manifest**를 선택합니다.
2. `manifest.yaml` 내용을 붙여넣어 앱을 생성합니다.
3. **OAuth & Permissions**에서 워크스페이스에 설치합니다.
4. Bot User OAuth Token(`xoxb-...`)을 복사합니다.
5. **Basic Information → App-Level Tokens**에서 `connections:write` 권한을 가진 App Token(`xapp-...`)을 만듭니다.
6. Socket Mode가 활성화되어 있는지 확인합니다.
7. 근태 채널과 스크럼 채널에 봇을 초대합니다.

Manifest가 요청하는 Bot Scope는 다음과 같습니다.

```text
commands
chat:write
users:read
```

## Railway 배포

1. Railway에서 이 GitHub 저장소를 배포합니다.
2. 같은 Railway 프로젝트에 PostgreSQL을 추가합니다.
3. 봇 서비스 Variables에 아래 값을 설정합니다.

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
DATABASE_URL=${{Postgres.DATABASE_URL}}
TIMEZONE=Asia/Seoul
WORK_CHANNEL_ID=C0123456789
SCRUM_CHANNEL_ID=C0123456789
LOG_LEVEL=INFO
```

`WORK_CHANNEL_ID`는 근태/시간 로그용 채널, `SCRUM_CHANNEL_ID`는 일일 업무보고 공개 채널입니다. 둘을 같은 채널로 지정해도 됩니다.

이 앱은 Socket Mode worker이므로 공개 HTTP 도메인이나 Request URL이 필요하지 않습니다.

## 로컬 실행

Python 3.12와 PostgreSQL이 필요합니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 환경변수 export 후
python app.py
```

Windows PowerShell에서는 `.env` 파일을 자동 로드하지 않으므로 `$env:SLACK_BOT_TOKEN=...` 형태로 설정하거나 별도 dotenv 로더를 사용하세요.

## 테스트

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py database.py slack_utils.py time_utils.py
```

## 보안/운영 주의

- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `DATABASE_URL`은 GitHub에 커밋하지 마세요.
- 실제 채널 ID만 환경변수에 넣고 사용자 입력으로 채널을 바꾸지 않습니다.
- 스크럼 초안은 Ephemeral 메시지로 검토하고 완성 시에만 공개합니다.
- 정시 알림은 PostgreSQL의 `hourly_prompts` 테이블로 중복 실행을 방지합니다.
- 현재 MVP는 관리자 수동 보정, CSV 내보내기, 휴가/연차 관리 기능은 포함하지 않습니다.

## 기존 Discord 버전과 차이

Discord 전용 interaction/view 코드는 제거하고 Slack Bolt + Socket Mode + Block Kit Modal로 교체했습니다. 데이터 모델과 핵심 근태 규칙은 동일한 방향을 유지하되, Slack Workspace/User ID가 문자열이므로 DB 키 타입을 Slack에 맞게 변경했습니다.

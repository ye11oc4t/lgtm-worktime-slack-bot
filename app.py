import asyncio
import json
import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from database import Database, WorktimeError
from slack_utils import (
    find_scrum_safety_issues,
    parse_user_mention,
    scrum_blocks,
    scrum_values,
)
from time_utils import format_duration, parse_local_date, parse_month, render_grass


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("lgtm-worktime-slack-bot")


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"환경변수 {name}가 필요합니다.")
    return value


BOT_TOKEN = required_env("SLACK_BOT_TOKEN")
APP_TOKEN = required_env("SLACK_APP_TOKEN")
DATABASE_URL = required_env("DATABASE_URL")
WORK_CHANNEL_ID = required_env("WORK_CHANNEL_ID")
SCRUM_CHANNEL_ID = os.getenv("SCRUM_CHANNEL_ID", WORK_CHANNEL_ID).strip() or WORK_CHANNEL_ID

try:
    TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Seoul"))
except ZoneInfoNotFoundError as exc:
    raise RuntimeError("TIMEZONE에 올바른 IANA 시간대를 입력하세요.") from exc

app = AsyncApp(token=BOT_TOKEN)
db = Database(DATABASE_URL)


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def workspace_id_from(payload: dict) -> str:
    team_id = payload.get("team_id") or payload.get("team", {}).get("id")
    if not team_id:
        raise WorktimeError("Slack 워크스페이스 ID를 확인할 수 없어요.")
    return str(team_id)


def is_channel(payload: dict, expected: str) -> bool:
    channel_id = payload.get("channel_id") or payload.get("channel", {}).get("id")
    return channel_id == expected


async def post_ephemeral(client, channel: str, user: str, text: str, blocks=None) -> None:
    await client.chat_postEphemeral(channel=channel, user=user, text=text, blocks=blocks)


async def public_message(client, channel: str, text: str, blocks=None) -> None:
    await client.chat_postMessage(channel=channel, text=text, blocks=blocks)


async def user_display_name(client, user_id: str) -> str:
    try:
        response = await client.users_info(user=user_id)
        user = response.get("user", {})
        profile = user.get("profile", {})
        return profile.get("display_name") or profile.get("real_name") or user.get("real_name") or user_id
    except Exception:
        logger.exception("사용자 이름 조회 실패: %s", user_id)
        return user_id


def status_label(status: str) -> str:
    return {"working": "근무 중", "on_break": "휴식 중", "completed": "퇴근 완료"}.get(status, status)


async def handle_error(client, channel: str, user: str, exc: Exception) -> None:
    if isinstance(exc, (WorktimeError, ValueError)):
        message = f"⚠️ {exc}"
    else:
        logger.exception("명령 처리 실패", exc_info=exc)
        message = "❌ 처리 중 오류가 발생했어요. 로그를 확인해 주세요."
    await post_ephemeral(client, channel, user, message)


def parse_target_and_value(text: str, default_user: str) -> tuple[str, str | None]:
    tokens = text.split()
    if not tokens:
        return default_user, None
    mentioned = parse_user_mention(tokens[0])
    if mentioned:
        return mentioned, tokens[1] if len(tokens) > 1 else None
    return default_user, tokens[0]


async def do_clock_in(client, command: dict) -> None:
    current = now_local()
    workspace_id = workspace_id_from(command)
    user_id = command["user_id"]
    await db.clock_in(workspace_id, user_id, current)
    await public_message(
        client,
        command["channel_id"],
        f"🟢 <@{user_id}> 출근! `{current:%Y-%m-%d %H:%M:%S}`",
    )


async def do_break(client, command: dict) -> None:
    current = now_local()
    workspace_id = workspace_id_from(command)
    user_id = command["user_id"]
    result = await db.toggle_break(workspace_id, user_id, current)
    if result["action"] == "started":
        text = f"☕ <@{user_id}> 휴식 시작! `{current:%H:%M:%S}`"
    else:
        text = (
            f"🔵 <@{user_id}> 휴식 종료! `{current:%H:%M:%S}`\n"
            f"이번 휴식 *{format_duration(result['break_seconds'])}* · "
            f"오늘 총 휴식 *{format_duration(result['total_break_seconds'])}*"
        )
    await public_message(client, command["channel_id"], text)


async def do_clock_out(client, command: dict) -> None:
    current = now_local()
    workspace_id = workspace_id_from(command)
    user_id = command["user_id"]
    result = await db.clock_out(workspace_id, user_id, current)
    auto_break = "\n진행 중이던 휴식은 자동 종료했어요." if result["closed_break"] else ""
    await public_message(
        client,
        command["channel_id"],
        f"🔴 <@{user_id}> 퇴근! `{current:%Y-%m-%d %H:%M:%S}`\n"
        f"실작업 *{format_duration(result['work_seconds'])}* · "
        f"총 휴식 *{format_duration(result['total_break_seconds'])}*{auto_break}",
    )


async def do_record(client, command: dict, args: str) -> None:
    current = now_local()
    target_user, date_text = parse_target_and_value(args, command["user_id"])
    target_date = parse_local_date(date_text, current.date())
    result = await db.get_record(workspace_id_from(command), target_user, target_date, current)
    if result is None:
        raise WorktimeError(f"{target_date:%Y-%m-%d} 기록이 없어요.")
    out = result["clock_out"]
    out_text = out.astimezone(TIMEZONE).strftime("%H:%M:%S") if out else "-"
    await public_message(
        client,
        command["channel_id"],
        f"📋 *<@{target_user}> · {target_date:%Y-%m-%d} 근무 기록*\n"
        f"상태: {status_label(result['status'])}\n"
        f"출근: `{result['clock_in'].astimezone(TIMEZONE):%H:%M:%S}` · 퇴근: `{out_text}`\n"
        f"총 휴식: *{format_duration(result['total_break_seconds'])}*\n"
        f"실작업: *{format_duration(result['work_seconds'])}*",
    )


async def do_grass(client, command: dict, args: str) -> None:
    current = now_local()
    target_user, month_text = parse_target_and_value(args, command["user_id"])
    year, month = parse_month(month_text, current.date())
    records = await db.get_month_records(
        workspace_id_from(command), target_user, year, month, current
    )
    seconds_by_day = {item["work_date"]: item["work_seconds"] for item in records}
    total_seconds = sum(seconds_by_day.values())
    worked_days = sum(1 for seconds in seconds_by_day.values() if seconds > 0)
    await public_message(
        client,
        command["channel_id"],
        f"🌱 *<@{target_user}> · {year}-{month:02d} 작업 잔디*\n"
        f"```{render_grass(year, month, seconds_by_day)}```\n"
        "`·` 범위 밖  `⬛` 0h  `🟦` <6h  `🟨` <12h  `🟧` <18h  `🟥` ≥18h\n"
        f"총 *{format_duration(total_seconds)}* · 작업일 *{worked_days}일*",
    )


HELP_TEXT = """*LGTM Worktime 명령어*
`/worktime 출근` 업무 시작
`/worktime 휴식` 휴식 시작/종료 토글
`/worktime 퇴근` 업무 종료 및 실작업시간 계산
`/worktime 기록 [@사용자] [YYYY-MM-DD]` 일별 기록
`/worktime 잔디 [@사용자] [YYYY-MM]` 월간 작업시간
`/setlog 내용` 현재 시간대 업무 기록
`/today [@사용자]` 오늘의 시간대별 업무 로그
`/scrum` 오늘 일일 업무보고 작성/수정/완성"""


@app.command("/worktime")
async def worktime_command(ack, command, client):
    await ack()
    user_id = command["user_id"]
    channel_id = command["channel_id"]
    if channel_id != WORK_CHANNEL_ID:
        await post_ephemeral(client, channel_id, user_id, f"⚠️ 근태 명령은 <#{WORK_CHANNEL_ID}> 채널에서만 사용할 수 있어요.")
        return
    raw = command.get("text", "").strip()
    action, _, args = raw.partition(" ")
    action = action.lower()
    try:
        if action in {"출근", "in", "clockin", "start"}:
            await do_clock_in(client, command)
        elif action in {"휴식", "break", "pause"}:
            await do_break(client, command)
        elif action in {"퇴근", "out", "clockout", "end"}:
            await do_clock_out(client, command)
        elif action in {"기록", "record"}:
            await do_record(client, command, args)
        elif action in {"잔디", "grass"}:
            await do_grass(client, command, args)
        elif action in {"", "도움", "help"}:
            await post_ephemeral(client, channel_id, user_id, HELP_TEXT)
        else:
            await post_ephemeral(client, channel_id, user_id, f"⚠️ 알 수 없는 하위 명령 `{action}`입니다.\n\n{HELP_TEXT}")
    except Exception as exc:
        await handle_error(client, channel_id, user_id, exc)


@app.command("/setlog")
async def setlog_command(ack, command, client):
    await ack()
    user_id = command["user_id"]
    channel_id = command["channel_id"]
    if channel_id != WORK_CHANNEL_ID:
        await post_ephemeral(client, channel_id, user_id, f"⚠️ 업무 로그는 <#{WORK_CHANNEL_ID}> 채널에서만 작성할 수 있어요.")
        return
    try:
        content = command.get("text", "").strip()
        result = await db.set_hourly_log(
            workspace_id_from(command), user_id, now_local(), content
        )
        action = "수정" if result["updated"] else "저장"
        await public_message(
            client,
            channel_id,
            f"📝 <@{user_id}> 업무 로그 {action}! "
            f"`{result['hour_start'].astimezone(TIMEZONE):%H:00}~{result['hour_end'].astimezone(TIMEZONE):%H:00}`\n"
            f"> {content}",
        )
    except Exception as exc:
        await handle_error(client, channel_id, user_id, exc)


@app.command("/today")
async def today_command(ack, command, client):
    await ack()
    user_id = command["user_id"]
    channel_id = command["channel_id"]
    if channel_id != WORK_CHANNEL_ID:
        await post_ephemeral(client, channel_id, user_id, f"⚠️ `/today`는 <#{WORK_CHANNEL_ID}> 채널에서만 사용할 수 있어요.")
        return
    try:
        raw = command.get("text", "").strip()
        target_user = parse_user_mention(raw) if raw else user_id
        if raw and not target_user:
            raise WorktimeError("사용자는 Slack 멘션 형식으로 입력해 주세요. 예: `/today @김서연`")
        current = now_local()
        record = await db.get_record(
            workspace_id_from(command), target_user, current.date(), current
        )
        if record is None:
            raise WorktimeError("오늘 근무 기록이 없어요.")
        logs = await db.get_hourly_logs(workspace_id_from(command), target_user, current.date())
        timeline = []
        for item in logs:
            start = item["hour_start"].astimezone(TIMEZONE)
            end = item["hour_end"].astimezone(TIMEZONE)
            timeline.append(f"`{start:%H:%M}~{end:%H:%M}` {item['content']}")
        if not timeline:
            timeline.append("아직 작성한 업무 로그가 없어요.")
        await public_message(
            client,
            channel_id,
            f"📅 *<@{target_user}> · 오늘 한 일*\n" + "\n".join(timeline) + "\n\n"
            f"상태: {status_label(record['status'])} · 실작업 *{format_duration(record['work_seconds'])}* · "
            f"총 휴식 *{format_duration(record['total_break_seconds'])}*",
        )
    except Exception as exc:
        await handle_error(client, channel_id, user_id, exc)


def modal_input(block_id: str, action_id: str, label: str, initial: str = "", optional: bool = False, max_length: int = 1000) -> dict:
    element = {
        "type": "plain_text_input",
        "action_id": action_id,
        "multiline": True,
        "max_length": max_length,
    }
    if initial:
        element["initial_value"] = initial
    return {
        "type": "input",
        "block_id": block_id,
        "optional": optional,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


@app.command("/scrum")
async def scrum_command(ack, command, client):
    await ack()
    user_id = command["user_id"]
    channel_id = command["channel_id"]
    if channel_id != SCRUM_CHANNEL_ID:
        await post_ephemeral(client, channel_id, user_id, f"⚠️ `/scrum`은 <#{SCRUM_CHANNEL_ID}> 채널에서만 사용할 수 있어요.")
        return
    try:
        current = now_local()
        workspace_id = workspace_id_from(command)
        record = await db.get_record(workspace_id, user_id, current.date(), current)
        if record is None:
            raise WorktimeError("오늘 근무 기록이 없어요. 먼저 출근해 주세요.")
        existing = await db.get_daily_scrum_report(workspace_id, user_id, current.date())
        if existing and existing["finalized_at"]:
            await post_ephemeral(client, channel_id, user_id, "✅ 오늘 업무보고는 이미 완성되어 공유됐어요.")
            return
        initial = dict(existing) if existing else {}
        metadata = json.dumps({
            "workspace_id": workspace_id,
            "user_id": user_id,
            "channel_id": channel_id,
            "work_date": current.date().isoformat(),
        })
        await client.views_open(
            trigger_id=command["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "scrum_modal",
                "private_metadata": metadata,
                "title": {"type": "plain_text", "text": "일일 업무보고"},
                "submit": {"type": "plain_text", "text": "초안 저장"},
                "close": {"type": "plain_text", "text": "취소"},
                "blocks": [
                    modal_input("module", "value", "담당 모듈 / 작업 영역", initial.get("module", ""), max_length=300),
                    modal_input("completed", "value", "완료한 일", initial.get("completed", "")),
                    modal_input("in_progress", "value", "진행 중인 일", initial.get("in_progress", "")),
                    modal_input("next_tasks", "value", "다음에 할 일", initial.get("next_tasks", "")),
                    modal_input("blockers_notes", "value", "어려웠던 점 / 비고", initial.get("blockers_notes", ""), optional=True),
                ],
            },
        )
    except Exception as exc:
        await handle_error(client, channel_id, user_id, exc)


@app.view("scrum_modal")
async def scrum_modal_submission(ack, body, view, client):
    metadata = json.loads(view["private_metadata"])
    values = view["state"]["values"]
    report = {
        key: values[key]["value"].get("value", "").strip()
        for key in ("module", "completed", "in_progress", "next_tasks", "blockers_notes")
    }
    issues = find_scrum_safety_issues(scrum_values(report))
    if issues:
        await ack(response_action="errors", errors={
            "blockers_notes": f"민감정보/전체멘션 감지: {', '.join(issues)}. 제거 후 다시 제출해 주세요."
        })
        return
    await ack()
    channel_id = metadata["channel_id"]
    user_id = metadata["user_id"]
    try:
        saved = await db.upsert_daily_scrum_report(
            metadata["workspace_id"],
            user_id,
            date.fromisoformat(metadata["work_date"]),
            **report,
        )
        display_name = await user_display_name(client, user_id)
        action_value = json.dumps({
            "workspace_id": metadata["workspace_id"],
            "user_id": user_id,
            "work_date": metadata["work_date"],
            "updated_at": saved["updated_at"].isoformat(),
        })
        blocks = scrum_blocks(display_name, date.fromisoformat(metadata["work_date"]), saved)
        blocks.extend([
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "finalize_scrum",
                        "text": {"type": "plain_text", "text": "완성 및 공유"},
                        "style": "primary",
                        "value": action_value,
                    }
                ],
            },
            {
                "type": "context",
                "elements": [{"type": "plain_text", "text": "완성 전에는 작성자에게만 보입니다. 완성 후 수정할 수 없습니다."}],
            },
        ])
        await post_ephemeral(client, channel_id, user_id, "스크럼 초안을 저장했습니다.", blocks=blocks)
    except Exception as exc:
        await handle_error(client, channel_id, user_id, exc)


@app.action("finalize_scrum")
async def finalize_scrum_action(ack, body, client):
    await ack()
    action = body["actions"][0]
    payload = json.loads(action["value"])
    user_id = body["user"]["id"]
    channel_id = body.get("channel", {}).get("id") or SCRUM_CHANNEL_ID
    if user_id != payload["user_id"]:
        await post_ephemeral(client, channel_id, user_id, "⚠️ 이 업무보고는 작성자만 완성할 수 있어요.")
        return
    try:
        work_date = date.fromisoformat(payload["work_date"])
        expected_updated_at = datetime.fromisoformat(payload["updated_at"])
        report = await db.claim_daily_scrum_publish(
            payload["workspace_id"], user_id, work_date, expected_updated_at
        )
        if report["already_published"]:
            await post_ephemeral(client, channel_id, user_id, "✅ 이미 공유된 업무보고예요.")
            return
        if report["busy"]:
            await post_ephemeral(client, channel_id, user_id, "⏳ 업무보고 전송이 이미 진행 중이에요.")
            return
        issues = find_scrum_safety_issues(scrum_values(report))
        if issues:
            raise WorktimeError(f"안전 점검을 통과하지 못했습니다: {', '.join(issues)}")
        display_name = await user_display_name(client, user_id)
        response = await client.chat_postMessage(
            channel=SCRUM_CHANNEL_ID,
            text=f"{display_name}님의 {work_date:%Y-%m-%d} 일일 업무보고",
            blocks=scrum_blocks(display_name, work_date, report),
        )
        await db.finish_scrum_publish(report["id"], response["ts"])
        await post_ephemeral(client, channel_id, user_id, "✅ 일일 업무보고를 스크럼 채널에 공유했습니다.")
    except Exception as exc:
        report_id = locals().get("report", {}).get("id") if isinstance(locals().get("report"), dict) else None
        if report_id:
            try:
                await db.fail_scrum_publish(report_id, str(exc))
            except Exception:
                logger.exception("스크럼 실패 상태 기록 오류")
        await handle_error(client, channel_id, user_id, exc)


async def hourly_prompt_loop(client, workspace_id: str) -> None:
    last_seen_minute: tuple[int, int, int, int, int] | None = None
    while True:
        try:
            current = now_local()
            marker = (current.year, current.month, current.day, current.hour, current.minute)
            if current.minute == 0 and marker != last_seen_minute:
                last_seen_minute = marker
                hour_start = current.replace(minute=0, second=0, microsecond=0)
                active_ids = await db.get_active_user_ids(workspace_id)
                if active_ids and await db.claim_hourly_prompt(workspace_id, hour_start):
                    mentions = " ".join(f"<@{user_id}>" for user_id in active_ids)
                    try:
                        await client.chat_postMessage(
                            channel=WORK_CHANNEL_ID,
                            text=(
                                f"⏰ {mentions}\n"
                                f"지금 무엇을 하고 있나요? `{hour_start:%H:00}~{(hour_start.hour + 1) % 24:02d}:00` 업무를 "
                                "`/setlog 내용`으로 남겨 주세요. 이 시간 안에는 언제 입력해도 같은 구간에 저장됩니다."
                            ),
                        )
                    except Exception:
                        await db.release_hourly_prompt(workspace_id, hour_start)
                        raise
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("정시 업무 로그 알림 실패")
        await asyncio.sleep(15)


async def main() -> None:
    await db.connect()
    await db.initialize()
    auth = await app.client.auth_test()
    workspace_id = auth["team_id"]
    prompt_task = asyncio.create_task(hourly_prompt_loop(app.client, workspace_id))
    handler = AsyncSocketModeHandler(app, APP_TOKEN)
    try:
        logger.info("Slack bot started for workspace %s", workspace_id)
        await handler.start_async()
    finally:
        prompt_task.cancel()
        await asyncio.gather(prompt_task, return_exceptions=True)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())

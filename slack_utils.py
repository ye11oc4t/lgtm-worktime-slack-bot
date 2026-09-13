import re
from collections.abc import Iterable, Mapping
from datetime import date


SENSITIVE_PATTERNS = (
    ("AWS Access Key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Slack 토큰", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Slack Webhook URL", re.compile(r"https://hooks\.slack\.com/services/\S+", re.I)),
    ("GitHub 토큰", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("개인키", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "비밀번호/토큰으로 보이는 값",
        re.compile(r"\b(?:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+", re.I),
    ),
)
MASS_MENTION_PATTERN = re.compile(r"(?:@channel|@everyone|@here|<!channel>|<!here>|<!everyone>)", re.I)
USER_MENTION_PATTERN = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")


def find_scrum_safety_issues(values: Iterable[str]) -> list[str]:
    text = "\n".join(values)
    issues: list[str] = []
    if MASS_MENTION_PATTERN.search(text):
        issues.append("전체 알림 멘션")
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            issues.append(label)
    return issues


def parse_user_mention(value: str) -> str | None:
    match = USER_MENTION_PATTERN.fullmatch(value.strip())
    return match.group(1) if match else None


def scrum_values(report: Mapping[str, str]) -> list[str]:
    return [
        report["module"],
        report["completed"],
        report["in_progress"],
        report["next_tasks"],
        report.get("blockers_notes", ""),
    ]


def scrum_blocks(display_name: str, work_date: date, report: Mapping[str, str]) -> list[dict]:
    sections = (
        ("담당 모듈 / 작업 영역", report["module"]),
        ("완료한 일", report["completed"]),
        ("진행 중인 일", report["in_progress"]),
        ("다음에 할 일", report["next_tasks"]),
        ("어려웠던 점 / 비고", report.get("blockers_notes", "") or "없음"),
    )
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"일일 업무보고 · {work_date:%Y-%m-%d}"},
        },
        {
            "type": "context",
            "elements": [{"type": "plain_text", "text": f"작성자: {display_name}"}],
        },
        {"type": "divider"},
    ]
    for heading, value in sections:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{heading}*\n{escape_mrkdwn(value)}"},
            }
        )
    return blocks


def escape_mrkdwn(value: str) -> str:
    # 사용자 입력의 Slack 특수 토큰 실행을 막는다.
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

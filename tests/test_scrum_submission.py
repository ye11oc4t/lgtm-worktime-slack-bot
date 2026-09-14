import importlib
import json
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


# Import registers handlers but does not start Socket Mode or connect to a DB.
with patch.dict(os.environ, {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "DATABASE_URL": "postgresql://localhost/test",
    "WORK_CHANNEL_ID": "CWORK",
    "SCRUM_CHANNEL_ID": "CSCRUM",
    "TIMEZONE": "Asia/Seoul",
}):
    bot = importlib.import_module("app")


class ScrumSubmissionTest(unittest.IsolatedAsyncioTestCase):
    def make_view(self, notes):
        values = {
            key: {"value": {"type": "plain_text_input", "value": "  업무 내용  "}}
            for key in ("module", "completed", "in_progress", "next_tasks")
        }
        values["blockers_notes"] = {"value": notes}
        return {
            "private_metadata": json.dumps({
                "workspace_id": "TTEST", "user_id": "UTEST",
                "channel_id": "CSCRUM", "work_date": "2026-09-14",
            }),
            "state": {"values": values},
        }

    async def test_optional_notes_save_and_preview(self):
        cases = [
            ({"value": None}, ""),
            ({"value": ""}, ""),
            ({}, ""),
            ({"value": "  확인 필요  "}, "확인 필요"),
        ]
        for notes, expected in cases:
            with self.subTest(notes=notes):
                ack = AsyncMock()

                async def save(*args, **report):
                    # Slack must receive its acknowledgement before database I/O.
                    ack.assert_awaited_once_with()
                    return {**report, "updated_at": datetime(2026, 9, 14, tzinfo=timezone.utc)}

                db = SimpleNamespace(upsert_daily_scrum_report=AsyncMock(side_effect=save))
                client = SimpleNamespace(
                    users_info=AsyncMock(return_value={"user": {"real_name": "테스트"}}),
                    chat_postEphemeral=AsyncMock(),
                )
                with patch.object(bot, "db", db):
                    await bot.scrum_modal_submission(ack, {}, self.make_view(notes), client)

                ack.assert_awaited_once_with()
                db.upsert_daily_scrum_report.assert_awaited_once_with(
                    "TTEST", "UTEST", date(2026, 9, 14),
                    module="업무 내용", completed="업무 내용", in_progress="업무 내용",
                    next_tasks="업무 내용", blockers_notes=expected,
                )
                client.chat_postEphemeral.assert_awaited_once()
                preview = client.chat_postEphemeral.call_args.kwargs
                self.assertEqual(preview["text"], "스크럼 초안을 저장했습니다.")
                self.assertEqual((preview["channel"], preview["user"]), ("CSCRUM", "UTEST"))
                sections = [block["text"]["text"] for block in preview["blocks"] if block["type"] == "section"]
                self.assertIn("*어려웠던 점 / 비고*\n" + (expected or "없음"), sections)
                buttons = [block for block in preview["blocks"] if block["type"] == "actions"]
                self.assertEqual(buttons[0]["elements"][0]["action_id"], "finalize_scrum")

    async def test_validation_still_acknowledges_without_saving(self):
        view = self.make_view({"value": None})
        view["state"]["values"]["completed"]["value"]["value"] = "@here"
        ack = AsyncMock()
        db = SimpleNamespace(upsert_daily_scrum_report=AsyncMock())
        client = SimpleNamespace(chat_postEphemeral=AsyncMock())
        with patch.object(bot, "db", db):
            await bot.scrum_modal_submission(ack, {}, view, client)
        ack.assert_awaited_once()
        self.assertEqual(ack.call_args.kwargs["response_action"], "errors")
        self.assertIn("전체 알림 멘션", ack.call_args.kwargs["errors"]["blockers_notes"])
        db.upsert_daily_scrum_report.assert_not_awaited()
        client.chat_postEphemeral.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

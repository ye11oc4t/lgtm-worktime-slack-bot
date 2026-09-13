import unittest
from datetime import date

from slack_utils import find_scrum_safety_issues, parse_user_mention
from time_utils import format_duration, parse_local_date, parse_month, grass_level


class TimeUtilsTest(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(3661), "1시간 1분 1초")

    def test_parse_date_and_month(self):
        default = date(2026, 9, 13)
        self.assertEqual(parse_local_date(None, default), default)
        self.assertEqual(parse_local_date("2026-09-12", default), date(2026, 9, 12))
        self.assertEqual(parse_month("2026-08", default), (2026, 8))

    def test_grass_levels(self):
        self.assertEqual(grass_level(0), "⬛")
        self.assertEqual(grass_level(3600), "🟦")
        self.assertEqual(grass_level(6 * 3600), "🟨")
        self.assertEqual(grass_level(12 * 3600), "🟧")
        self.assertEqual(grass_level(18 * 3600), "🟥")


class SlackUtilsTest(unittest.TestCase):
    def test_parse_user_mention(self):
        self.assertEqual(parse_user_mention("<@U123ABC>"), "U123ABC")
        self.assertEqual(parse_user_mention("<@U123ABC|name>"), "U123ABC")
        self.assertIsNone(parse_user_mention("@name"))

    def test_safety_detection(self):
        self.assertEqual(find_scrum_safety_issues(["정상 업무 내용"]), [])
        issues = find_scrum_safety_issues(["@here", "token=super-secret-value"])
        self.assertIn("전체 알림 멘션", issues)
        self.assertIn("비밀번호/토큰으로 보이는 값", issues)


if __name__ == "__main__":
    unittest.main()

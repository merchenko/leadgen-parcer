import unittest
from unittest.mock import patch

from freelancer_bot.config import RuntimeConfig


class RuntimeConfigTest(unittest.TestCase):
    def test_accepts_legacy_parser_env_names(self):
        env = {
            "API_ID": "12345",
            "API_HASH": "hash",
            "BOT_TOKEN": "token",
            "TARGET_USER_ID": "98765",
        }

        with patch.dict("os.environ", env, clear=True):
            config = RuntimeConfig.from_env()

        self.assertEqual(config.api_id, 12345)
        self.assertEqual(config.api_hash, "hash")
        self.assertEqual(config.bot_token, "token")
        self.assertEqual(config.target_chat_id, 98765)

    def test_prefers_new_env_names_over_legacy_names(self):
        env = {
            "TELEGRAM_API_ID": "111",
            "TELEGRAM_API_HASH": "new_hash",
            "TELEGRAM_BOT_TOKEN": "new_token",
            "TELEGRAM_TARGET_CHAT_ID": "222",
            "API_ID": "333",
            "API_HASH": "old_hash",
            "BOT_TOKEN": "old_token",
            "TARGET_USER_ID": "444",
        }

        with patch.dict("os.environ", env, clear=True):
            config = RuntimeConfig.from_env()

        self.assertEqual(config.api_id, 111)
        self.assertEqual(config.api_hash, "new_hash")
        self.assertEqual(config.bot_token, "new_token")
        self.assertEqual(config.target_chat_id, 222)


if __name__ == "__main__":
    unittest.main()


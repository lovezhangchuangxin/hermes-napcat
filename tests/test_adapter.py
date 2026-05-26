from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERMES_AGENT = Path(os.environ.get("HERMES_AGENT_PATH", "~/.hermes/hermes-agent")).expanduser()
sys.path.insert(0, str(HERMES_AGENT))
sys.path.insert(0, str(ROOT))

import adapter
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry


class _Ctx:
    def register_platform(self, **kwargs):
        platform_registry.register(PlatformEntry(source="plugin", **kwargs))


adapter.register(_Ctx())


def _make_adapter(extra=None):
    cfg = PlatformConfig(enabled=True, extra=extra or {"mode": "reverse"})
    return adapter.NapCatAdapter(cfg)


class AdapterHelpersTest(unittest.TestCase):
    def tearDown(self):
        for key in ("NAPCAT_ALLOWED_USERS", "NAPCAT_ALLOW_ALL_USERS"):
            os.environ.pop(key, None)

    def test_chat_id_normalization(self):
        self.assertEqual(adapter._normalize_chat_id("123"), "group:123")
        self.assertEqual(adapter._normalize_chat_id("123", default_kind="private"), "private:123")
        self.assertEqual(adapter._normalize_chat_id("dm:456"), "private:456")
        self.assertEqual(adapter._parse_chat_id("g:789"), ("group", "789"))

    def test_group_gate_defaults_to_mention_required(self):
        bot = _make_adapter({"mode": "reverse", "self_id": "10001"})
        self.assertFalse(bot._should_accept_group_message("123", "hello", False))
        self.assertTrue(bot._should_accept_group_message("123", "hello", True))

    def test_group_gate_free_response_group(self):
        bot = _make_adapter({"mode": "reverse", "free_response_groups": ["group:123"]})
        self.assertTrue(bot._should_accept_group_message("123", "hello", False))

    def test_cq_string_parsing(self):
        bot = _make_adapter({"mode": "reverse", "self_id": "10001", "download_media": False})
        parsed = asyncio.run(
            bot._cq_string_to_text_and_media("[CQ:reply,id=9][CQ:at,qq=10001] hello [CQ:face,id=14]")
        )
        self.assertTrue(parsed["mentioned_self"])
        self.assertEqual(parsed["reply_to_message_id"], "9")
        self.assertEqual(parsed["text"], "hello [face:14]")

    def test_segment_parsing(self):
        bot = _make_adapter({"mode": "reverse", "self_id": "10001", "download_media": False})
        parsed = asyncio.run(
            bot._segments_to_text_and_media(
                [
                    {"type": "reply", "data": {"id": 7}},
                    {"type": "at", "data": {"qq": "10001"}},
                    {"type": "text", "data": {"text": " ping"}},
                    {"type": "image", "data": {"url": "https://example.invalid/a.jpg"}},
                ]
            )
        )
        self.assertTrue(parsed["mentioned_self"])
        self.assertEqual(parsed["reply_to_message_id"], "7")
        self.assertEqual(parsed["text"], "ping")
        self.assertEqual(parsed["media_urls"], [])

    def test_yaml_auth_config_bridges_to_env(self):
        seeded = adapter._apply_yaml_config(
            {},
            {
                "allow_from": ["10001", "10002"],
                "allow_all_users": False,
            },
        )

        self.assertEqual(os.environ["NAPCAT_ALLOWED_USERS"], "10001,10002")
        self.assertEqual(os.environ["NAPCAT_ALLOW_ALL_USERS"], "false")
        self.assertEqual(seeded["allow_from"], ["10001", "10002"])
        self.assertFalse(seeded["allow_all_users"])


class AdapterSendTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_group_message_uses_onebot_action(self):
        bot = _make_adapter({"mode": "reverse"})
        calls = []

        async def fake_call_action(action, params, *, timeout=30.0):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0, "data": {"message_id": 42}}

        bot._call_action = fake_call_action
        result = await bot.send("group:123", "hello", reply_to="9")

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "42")
        self.assertEqual(calls[0][0], "send_group_msg")
        self.assertEqual(calls[0][1]["group_id"], 123)
        self.assertEqual(calls[0][1]["message"][0]["type"], "reply")
        self.assertEqual(calls[0][1]["message"][1]["data"]["text"], "hello")


if __name__ == "__main__":
    unittest.main()

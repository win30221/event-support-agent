import os
import unittest
from unittest.mock import patch

from request_pacing import RequestPacer, configured_rpm, note_rate_limit, pace_request, ENV


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.waits = []
        def sleep(seconds):
            self.waits.append(seconds)
            self.now += seconds
        self.pacer = RequestPacer(clock=lambda: self.now, sleeper=sleep)

    def test_spacing_shared_between_chat_and_embedding(self):
        with patch.dict(os.environ, {ENV: "20"}), patch("request_pacing._pacer", self.pacer):
            self.assertEqual(pace_request("nvidia"), 0)
            self.assertEqual(pace_request("openai"), 0)
            self.assertEqual(pace_request("nvidia"), 3)
            self.now += 1
            self.assertEqual(pace_request("nvidia"), 2)
        self.assertEqual(self.waits, [3, 2])

    def test_idle_time_does_not_create_extra_wait(self):
        self.pacer.wait(20)
        self.now += 7
        self.assertEqual(self.pacer.wait(20), 0)

    def test_rate_limit_defers_next_request(self):
        with patch.dict(os.environ, {ENV: "20"}), patch("request_pacing._pacer", self.pacer):
            pace_request("nvidia")
            note_rate_limit("nvidia", {"Retry-After": "9"})
            self.assertEqual(pace_request("nvidia"), 9)
            note_rate_limit("nvidia")
            self.assertEqual(pace_request("nvidia"), 60)

    def test_disabled_or_invalid_setting(self):
        with patch.dict(os.environ, {ENV: "0"}), patch("request_pacing._pacer", self.pacer):
            pace_request("nvidia")
            pace_request("nvidia")
            self.assertEqual(self.waits, [])
        with patch.dict(os.environ, {ENV: "-1"}):
            with self.assertRaises(ValueError):
                configured_rpm()

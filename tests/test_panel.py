"""Panel push contracts; the panel is a local stub and the snapshot is synthetic."""
import datetime as dt
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from jobs import panel, state

TOKEN = "test-only-token"
NOW = dt.datetime(2026, 10, 23, 16, 0, tzinfo=dt.timezone.utc)
SNAPSHOT = {"schema_version": 1, "generated_at": "2026-10-23T16:00:00Z", "as_of": "2026-10-24", "currency": "CNY",
            "accounts": [], "balances": [], "monthly": [], "net_worth": [], "transactions": []}


class StubPanel(HTTPServer):
    def __init__(self, responses):
        super().__init__(("127.0.0.1", 0), StubHandler)
        self.responses = list(responses)
        self.requests = []

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/api/v1/ingest/ledger"


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        status, payload = self.server.responses.pop(0)
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        if 300 <= status < 400:
            self.send_header("Location", "/elsewhere")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class PanelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        environment = patch.dict(os.environ, {"LEDGER_STATE_DIR": temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("LEDGER_PANEL_INGEST_TOKEN", "LEDGER_PANEL_INGEST_URL"):
            os.environ.pop(name, None)

    def stub(self, responses):
        server = StubPanel(responses)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_settings_need_a_token_and_https(self):
        self.assertIsNone(panel.settings())
        with patch.dict(os.environ, {"LEDGER_PANEL_INGEST_TOKEN": TOKEN}):
            self.assertEqual(panel.settings()["url"], panel.DEFAULT_URL)
            for url in ("http://lucius7.dev/api/v1/ingest/ledger", "https://"):
                with patch.dict(os.environ, {"LEDGER_PANEL_INGEST_URL": url}), self.assertRaises(panel.PanelPushError):
                    panel.settings()

    def test_digest_ignores_generation_time(self):
        later = dict(SNAPSHOT, generated_at="2026-10-23T17:00:00Z")
        self.assertEqual(panel.digest(SNAPSHOT), panel.digest(later))
        self.assertNotEqual(panel.digest(SNAPSHOT), panel.digest(dict(SNAPSHOT, as_of="2026-10-25")))

    def test_push_sends_bearer_json_and_skips_unchanged_content(self):
        server = self.stub([(200, {"data": {"stored": True, "bytes": 10}})])
        config = {"url": server.url, "token": TOKEN}
        self.assertEqual(panel.push(SNAPSHOT, config, NOW)["stored"], True)
        request = server.requests[0]
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(request["body"]), SNAPSHOT)
        self.assertIsNone(panel.push(dict(SNAPSHOT, generated_at="later"), config, NOW))
        self.assertEqual(len(server.requests), 1)
        self.assertNotIn(TOKEN, json.dumps(state.load("panel")))

    def test_failures_are_recorded_and_back_off(self):
        server = self.stub([(401, {"error": {"code": "unauthorized", "message": "bad token"}}), (200, {"data": {"stored": True}})])
        config = {"url": server.url, "token": TOKEN}
        with self.assertRaises(panel.PanelPushError) as caught:
            panel.push(SNAPSHOT, config, NOW)
        self.assertIn("401", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(state.load("panel")["failures"], 1)
        self.assertIsNone(panel.push(SNAPSHOT, config, NOW + dt.timedelta(seconds=60)))
        self.assertEqual(panel.push(SNAPSHOT, config, NOW + dt.timedelta(seconds=301))["stored"], True)
        self.assertEqual(state.load("panel")["failures"], 0)

    def test_server_errors_retry_and_redirects_fail(self):
        server = self.stub([(503, {}), (429, {}), (200, {"data": {"stored": False}})])
        delays = []
        self.assertEqual(panel.send(SNAPSHOT, server.url, TOKEN, sleep=delays.append), {"stored": False})
        self.assertEqual(delays, [2, 4])
        server = self.stub([(301, {}), (200, "<!doctype html>")])
        with self.assertRaises(panel.PanelPushError) as caught:
            panel.send(SNAPSHOT, server.url, TOKEN, sleep=lambda seconds: None)
        self.assertIn("301", str(caught.exception))
        with self.assertRaises(panel.PanelPushError) as caught:
            panel.send(SNAPSHOT, server.url, TOKEN, sleep=lambda seconds: None)
        self.assertIn("not JSON", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from decision_patterns.log_triage import LogTriage

SEEN = []


class FakeDecider(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SEEN.append(body)
        reply = {
            "answers": {
                "severity": {
                    "type": "choice",
                    "choice": "action_needed",
                    "probabilities": {"noise": 0.02, "info": 0.1, "action_needed": 0.8, "critical": 0.08},
                    "confidence": 0.73,
                }
            },
            "latency_ms": 3800.0,
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TriageHttpTest(unittest.TestCase):
    def test_round_trip(self):
        server = HTTPServer(("127.0.0.1", 0), FakeDecider)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            triage = LogTriage(f"http://127.0.0.1:{server.server_port}")
            result = triage("source=netdata alarm=disk_space_usage status=WARNING value=97%")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result.choice, "action_needed")
        self.assertEqual(result.route, "notify")
        self.assertEqual(result.latency_ms, 3800.0)
        self.assertEqual(SEEN[0]["questions"]["severity"]["type"], "choice")


if __name__ == "__main__":
    unittest.main()

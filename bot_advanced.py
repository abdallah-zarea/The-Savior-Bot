"""Legacy Savior Bot decommission stub.

The production bot has moved to the private savior-bot-v2 repository.
This service intentionally does not initialize Telegram or read any bot token.
Keeping a tiny HTTP process alive prevents Render from restarting the legacy
service while guaranteeing that the compromised legacy token is never used.
"""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class DecommissionedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Legacy Savior Bot is decommissioned."
        self.send_response(410)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), DecommissionedHandler).serve_forever()

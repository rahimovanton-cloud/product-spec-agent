import os, logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO)
PORT = int(os.getenv("PORT", 8080))

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a): pass

logging.info(f"Starting on port {PORT}")
HTTPServer(("0.0.0.0", PORT), H).serve_forever()

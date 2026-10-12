"""A tiny in-process stand-in for the Telegram Bot API, for tests."""
import email.parser
import email.policy
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456:TEST-token_abc"


class FakeTelegram:
    def __init__(self):
        self.calls = []       # (method, fields, files) — files: {name: (filename, bytes)}
        self.scripted = []    # responses to serve before behaving normally
        self.methods = {}     # method -> function(fields) -> result, for real answers
        self.files = {}       # file_path -> bytes, downloadable under /file/bot<token>/
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                prefix = "/bot%s/" % TOKEN
                if not self.path.startswith(prefix):
                    return self._reply(401, {"ok": False, "error_code": 401,
                                             "description": "Unauthorized"})
                method = self.path[len(prefix):]
                fields, files = {}, {}
                ctype = self.headers.get("Content-Type", "")
                if ctype.startswith("multipart/form-data"):
                    raw = b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body
                    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        filename = part.get_param("filename", header="content-disposition")
                        payload = part.get_payload(decode=True)
                        if filename is not None:
                            files[name] = (filename, payload)
                        else:
                            fields[name] = payload.decode("utf-8")
                else:
                    fields = dict(urllib.parse.parse_qsl(body.decode("utf-8"),
                                                         keep_blank_values=True))
                fake.calls.append((method, fields, files))
                if fake.scripted:
                    status, payload = fake.scripted.pop(0)
                    return self._reply(status, payload)
                if method in fake.methods:
                    return self._reply(200, {"ok": True, "result": fake.methods[method](fields)})
                self._reply(200, {"ok": True, "result": {"message_id": len(fake.calls)}})

            def do_GET(self):
                prefix = "/file/bot%s/" % TOKEN
                path = urllib.parse.unquote(self.path[len(prefix):])
                data = fake.files.get(path) if self.path.startswith(prefix) else None
                fake.calls.append(("GET", {"path": path}, {}))
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from .service import TouchSightService


MAX_REQUEST_BYTES = 16 * 1024 * 1024


def run_server(host: str, port: int, project_root: Path) -> None:
    web_root = (project_root / "web").resolve()
    service = TouchSightService(project_root / "output")

    class Handler(BaseHTTPRequestHandler):
        server_version = "TouchSightEDU/0.1"

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _request_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length 无效") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("请求为空或超过 16 MB")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("请求 JSON 无法解析") from exc
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON object")
            return payload

        def _file(self, path: Path) -> None:
            try:
                resolved = path.resolve()
                resolved.relative_to(web_root)
            except (OSError, ValueError):
                self.send_error(404)
                return
            if not resolved.is_file():
                self.send_error(404)
                return
            body = resolved.read_bytes()
            mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/status":
                self._json(service.status())
                return
            if path == "/api/state":
                self._json(service.snapshot())
                return
            if path == "/api/history":
                self._json(service.list_history())
                return
            relative = "index.html" if path == "/" else path.lstrip("/")
            self._file(web_root / relative)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._request_json()
                if path == "/api/direct":
                    self._json(service.compile_direct(
                        str(payload.get("image_data_url") or ""), payload
                    ))
                    return
                if path == "/api/teaching":
                    self._json(service.compile_teaching(payload))
                    return
                if path == "/api/teaching/image":
                    self._json(service.compile_teaching_image(payload))
                    return
                if path == "/api/history/restore":
                    self._json(service.restore_history(str(payload.get("history_id") or "")))
                    return
                if path == "/api/revise":
                    self._json(service.revise(str(payload.get("command") or ""), payload))
                    return
                if path == "/api/hardware/publish":
                    self._json(service.publish_hardware())
                    return
                self._json({"ok": False, "error": "接口不存在"}, 404)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except RuntimeError as exc:
                self._json({"ok": False, "error": str(exc)}, 502)
            except Exception as exc:
                self._json({"ok": False, "error": f"服务内部错误：{exc}"}, 500)

        def log_message(self, format: str, *args) -> None:
            print(f"[TouchSight EDU] {self.address_string()} - {format % args}")

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"TouchSight EDU: http://127.0.0.1:{port}")
    print("Software simulator · 40x50 · 2000 pins · H0/H1/H2/H3 = 0/2/4/6 mm")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

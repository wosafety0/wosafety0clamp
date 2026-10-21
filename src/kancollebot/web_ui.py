"""Minimal local-only dark web console for KancolleBot.

The first UI slice exposes service and read-only Poi bridge diagnostics only.
Game-control actions remain disabled until a verified runner lifecycle API exists.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError


_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KancolleBot · Local Console</title>
<style>
:root{color-scheme:dark;--bg:#101116;--panel:#191b23;--panel2:#20232d;--line:#303440;--text:#edf0f7;--muted:#969eaf;--green:#68dba0;--amber:#f0c674;--red:#ff7d86;--blue:#8db7ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#1b2231 0%,var(--bg) 48%);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:28px 20px 48px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px}
.kicker{color:var(--blue);font:11px ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.15em;text-transform:uppercase}
h1{font-size:25px;letter-spacing:-.04em;margin:4px 0 0}.pill{border:1px solid var(--line);border-radius:999px;padding:5px 10px;color:var(--muted);font-size:12px;white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.panel{background:linear-gradient(145deg,#1b1e27,#171920);border:1px solid var(--line);border-radius:14px;padding:18px;min-width:0}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.panel h2{font-size:13px;color:#b8c0d1;font-weight:600;margin:0}
.value{font-size:19px;font-weight:650;letter-spacing:-.02em}.muted{color:var(--muted)}.status{display:inline-flex;align-items:center;gap:8px}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);box-shadow:0 0 0 4px #ffffff08}.ok{color:var(--green)}.ok .dot{background:var(--green);box-shadow:0 0 0 4px #68dba018}.warn{color:var(--amber)}.warn .dot{background:var(--amber)}.bad{color:var(--red)}.bad .dot{background:var(--red)}
.controls{display:flex;gap:10px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:var(--panel2);color:#737b8c;border-radius:9px;padding:9px 13px;font:inherit;cursor:not-allowed}.btn.primary{border-color:#3d5a4c;background:#20372d;color:#86b99e}
.notice{margin-top:14px;color:var(--muted);font-size:12px}.log{font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;color:#b7c0d1;white-space:pre-wrap;overflow-wrap:anywhere;min-height:42px}
.footer{margin-top:18px;color:#727b8d;font-size:11px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
@media(max-width:640px){main{padding:20px 14px}.grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.value{font-size:17px}}
</style>
</head>
<body><main>
<header class="top"><div><div class="kicker">Local automation console</div><h1>KancolleBot</h1></div><div class="pill">LOCAL · READ-ONLY DIAGNOSTICS</div></header>
<section class="grid">
<article class="panel"><div class="panel-head"><h2>BOT SERVICE</h2><span class="pill">WEB UI</span></div><div class="status ok"><span class="dot"></span><span class="value">Console online</span></div><p class="muted">控制台服务已启动。自动化执行器尚未接入。</p></article>
<article class="panel"><div class="panel-head"><h2>POI BRIDGE</h2><span id="poi-tag" class="pill">CHECKING</span></div><div id="poi-status" class="status"><span class="dot"></span><span class="value">正在检查</span></div><p id="poi-detail" class="muted">仅查询本机 Poi MCP 健康端点，不执行游戏操作。</p></article>
<article class="panel"><div class="panel-head"><h2>CONTROL</h2><span class="pill">NOT CONNECTED</span></div><div class="controls"><button class="btn primary" disabled title="Runner lifecycle API is not integrated">启动</button><button class="btn" disabled title="Runner lifecycle API is not integrated">暂停</button><button class="btn" disabled title="Runner lifecycle API is not integrated">停止</button></div><p class="notice">控制按钮暂不可用：必须先接入并验证真实执行器生命周期接口，避免按钮显示成功但实际没有控制任务。</p></article>
<article class="panel"><div class="panel-head"><h2>RECENT DIAGNOSTIC</h2><span id="updated" class="pill">尚未刷新</span></div><div id="diagnostic" class="log">等待首次检查…</div><p class="notice">舰队状态、远征目标和剩余时间继续由 Poi 原生界面展示，不在这里重复呈现。</p></article>
</section>
<div class="footer"><span>Python standard library · No frontend dependencies</span><span>Loopback only · No gameplay actions</span></div>
</main>
<script>
async function refreshStatus(){
 const tag=document.getElementById("poi-tag"),status=document.getElementById("poi-status"),detail=document.getElementById("poi-detail"),diag=document.getElementById("diagnostic"),updated=document.getElementById("updated");
 try{
  const response=await fetch("/api/status",{cache:"no-store"});
  const data=await response.json();
  tag.textContent=data.poi_bridge.state.toUpperCase();
  status.className="status "+(data.poi_bridge.state==="connected"?"ok":data.poi_bridge.state==="error"?"bad":"warn");
  status.lastElementChild.textContent=data.poi_bridge.state==="connected"?"健康端点可访问":data.poi_bridge.state==="error"?"连接失败":"尚未验证";
  detail.textContent=data.poi_bridge.message;
  diag.textContent=data.poi_bridge.checked_at+"\n"+data.poi_bridge.message;
  updated.textContent="刷新于 "+new Date().toLocaleTimeString();
 }catch(error){
  tag.textContent="ERROR";status.className="status bad";status.lastElementChild.textContent="控制台 API 不可用";detail.textContent="无法读取本地状态接口。";diag.textContent=String(error);updated.textContent="刷新失败";
 }
}
refreshStatus();setInterval(refreshStatus,5000);
</script></body></html>"""


def build_status(client_factory: Any = PoiMcpClient) -> dict[str, Any]:
    """Return a sanitized status payload; never include raw bridge response data."""
    from datetime import datetime, timezone

    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        health = client_factory(timeout=1.5).health()
        healthy = health.get("status") == "ok" and health.get("storeReady") is True
        if healthy:
            state, message = "connected", "Poi MCP 健康端点可访问，storeReady=true。"
        else:
            state, message = "unverified", "健康端点有响应，但服务状态或 storeReady 未通过验证。"
    except (PoiMcpError, OSError, ValueError) as exc:
        state, message = "error", f"无法连接本机 Poi MCP 健康端点：{type(exc).__name__}。"
    return {
        "service": "online",
        "poi_bridge": {"state": state, "message": message, "checked_at": checked_at},
        "game_actions_enabled": False,
    }


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Create a loopback-only HTTP server for the local dashboard."""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("web console must bind to a loopback host")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")

    class Handler(BaseHTTPRequestHandler):
        server_version = "KancolleBotConsole/0.1"

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                body = _HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif path == "/api/status":
                body = json.dumps(build_status(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            elif path == "/api/health":
                body = b'{"status":"ok","game_actions_enabled":false}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                body = b'{"error":"not_found"}'
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.send_error(405, "Control endpoints are not available in this UI slice")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    server = create_server()
    print("KancolleBot local console: http://127.0.0.1:%d/" % server.server_port)
    print("Read-only diagnostics only; gameplay controls are disabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

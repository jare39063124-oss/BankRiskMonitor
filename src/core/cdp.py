"""Chrome DevTools Protocol 轻量客户端：读取浏览器登录态 Cookie + 驱动页面抓取。

用途：用户用「小号」在专用浏览器配置目录中登录抖音/快手/小红书/微博后，
本模块通过 CDP（--remote-debugging-port=9222）完成两件事：

1. ``read_cookies``：读取该配置目录的 Cookie，供社媒 Provider 注入请求头；
2. ``fetch_rendered``：直接**在已登录的浏览器里打开目标页面并取渲染后的 HTML**。
   抖音/快手/小红书的搜索结果由 JS 异步渲染，单纯 HTTP 请求拿到的只是空壳页，
   必须驱动真实浏览器才能拿到数据块，这是社媒源「真正接进来」的关键。

仅依赖标准库（socket/base64/hashlib/json），不引入 websocket-client。
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse

DEFAULT_PORT = 9222

# 常见浏览器可执行文件位置（按优先级）
_BROWSER_CANDIDATES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    """返回本机可用的 Chrome/Edge 可执行文件路径；找不到返回空串。"""
    for pat in _BROWSER_CANDIDATES:
        path = os.path.expandvars(pat)
        if path and os.path.exists(path):
            return path
    return ""


def is_debug_port_open(port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """检测本机调试端口是否已被浏览器占用。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def launch_browser(port: int = DEFAULT_PORT, profile_dir: str = "") -> tuple:
    """以调试端口启动浏览器（独立配置目录），返回 ``(ok, message)``。

    独立配置目录让登录态长期保留在本机，程序重启后仍可直接读取 Cookie。
    """
    exe = find_browser()
    if not exe:
        return False, "未找到 Chrome/Edge 可执行文件，请手动用 --remote-debugging-port 参数启动浏览器"
    if not profile_dir:
        return False, "未指定浏览器配置目录"
    try:
        os.makedirs(profile_dir, exist_ok=True)
    except OSError as e:
        return False, f"创建配置目录失败：{e}"
    args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}",
            "--no-first-run", "--no-default-browser-check"]
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return False, f"启动浏览器失败：{e}"
    return True, f"已启动调试浏览器（端口 {port}，配置目录 {profile_dir}），请在该窗口登录小号后回到程序点「读取登录态」"


class WebSocket:
    """最小 WebSocket 客户端（RFC 6455），够用即可：文本消息 + 掩码发送。"""

    def __init__(self, url: str, timeout: float = 5.0):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise RuntimeError(f"不支持的 WebSocket 地址：{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP 握手失败：连接被关闭")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        if b"101" not in header.split(b"\r\n")[0]:
            raise RuntimeError("CDP 握手被拒绝（浏览器未以调试端口启动？）")
        self._buf = rest

    # ---- 内部工具 ----
    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise RuntimeError("CDP 连接已关闭")
            data += chunk
        return data

    def _send_frame(self, payload: bytes, opcode: int = 0x1):
        header = bytearray()
        header.append(0x80 | opcode)
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    # ---- 对外 ----
    def send(self, obj: dict):
        self._send_frame(json.dumps(obj).encode("utf-8"))

    def recv(self) -> dict:
        """接收一条完整的 JSON 消息（自动跳过 ping / 处理分片）。"""
        payload = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(ln)
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:  # ping → pong
                self._send_frame(data, 0xA)
                continue
            if opcode == 0x8:
                raise RuntimeError("CDP 连接已关闭")
            payload += data
            if fin:
                break
        return json.loads(payload.decode("utf-8", "replace"))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def read_cookies(port: int = DEFAULT_PORT, timeout: float = 8.0) -> list:
    """通过 CDP 读取浏览器全部 Cookie，返回 ``[{name, value, domain, path}, ...]``。"""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=timeout) as resp:
            version = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        raise RuntimeError(f"未检测到调试浏览器（端口 {port}）：{e}")
    ws_url = version.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("浏览器未提供 CDP 调试地址")
    ws = WebSocket(ws_url, timeout=timeout)
    try:
        cookies = []
        for method, msg_id in (("Storage.getCookies", 1), ("Network.getAllCookies", 2)):
            ws.send({"id": msg_id, "method": method})
            while True:
                msg = ws.recv()
                if msg.get("id") != msg_id:
                    continue
                if "error" in msg:
                    break
                result = msg.get("result") or {}
                cookies = result.get("cookies") or []
                if cookies:
                    return cookies
                break
        if cookies:
            return cookies
        raise RuntimeError("CDP 未返回 Cookie（浏览器可能未登录任何站点）")
    finally:
        ws.close()


def cookie_header_for(cookies: list, domains) -> str:
    """按域名筛选 Cookie 并拼成 ``a=1; b=2`` 头字符串。"""
    if isinstance(domains, str):
        domains = [domains]
    wanted = [d.strip().lstrip(".").lower() for d in domains if d and d.strip()]
    picked = []
    for c in cookies or []:
        dom = str(c.get("domain") or "").lstrip(".").lower()
        if not any(dom == w or dom.endswith("." + w) for w in wanted):
            continue
        picked.append(f"{c.get('name')}={c.get('value')}")
    return "; ".join(picked)


# ---------------------------------------------------------------------------
# 页面抓取（驱动已登录的真实浏览器）
# ---------------------------------------------------------------------------

def _http_json(url: str, timeout: float = 5.0):
    """调用浏览器调试 HTTP 端点并解析 JSON。"""
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class PageSession:
    """一次页面抓取会话：新建标签页 → 导航 → 等待渲染 → 取 HTML → 关闭。

    使用 CDP 的 ``Target.createTarget`` / ``Page.navigate`` / ``Runtime.evaluate``
    三步，事件驱动等待 ``Page.loadEventFired``，随后再额外等待一段时间让
    前端异步接口回填（抖音/快手的搜索结果是 XHR 之后才渲染的）。
    """

    def __init__(self, ws_url: str, timeout: float = 20.0):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self.timeout = timeout
        self._msg_id = 0
        self._target_id = None
        self._session_id = None

    # ---- 底层 ----
    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send(self, method: str, params: dict = None, session_id: str = None) -> int:
        msg = {"id": self._next_id(), "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(msg)
        return msg["id"]

    def _wait_for(self, msg_id: int, timeout: float = None) -> dict:
        """等待指定 id 的响应（期间忽略事件消息）。"""
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            msg = self.ws.recv()
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP 调用失败：{msg['error']}")
                return msg.get("result") or {}
        raise RuntimeError("CDP 等待响应超时")

    # ---- 对外 ----
    def open(self, url: str, settle_ms: int = 2500) -> str:
        """打开 URL 并返回渲染后的 HTML 文本。"""
        result = self._wait_for(self._send("Target.createTarget", {"url": "about:blank"}))
        self._target_id = result.get("targetId")
        if not self._target_id:
            raise RuntimeError("CDP 未能创建标签页")

        attached = self._wait_for(self._send("Target.attachToTarget",
                                             {"targetId": self._target_id, "flatten": True}))
        self._session_id = attached.get("sessionId")
        if not self._session_id:
            raise RuntimeError("CDP 附着标签页失败")

        sid = self._session_id
        self._wait_for(self._send("Page.enable", {}, sid))
        self._wait_for(self._send("Runtime.enable", {}, sid))
        self._wait_for(self._send("Page.navigate", {"url": url}, sid))
        # 等待页面加载（loadEventFired 事件），超时则继续（部分站点为长连接页面）
        try:
            self._wait_for_load(sid)
        except RuntimeError:
            pass
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        html = self._evaluate("document.documentElement.outerHTML", sid)
        return html or ""

    def _wait_for_load(self, sid: str, timeout: float = None):
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            msg = self.ws.recv()
            if msg.get("method") == "Page.loadEventFired" and \
                    (msg.get("sessionId") == sid or not msg.get("sessionId")):
                return True
        raise RuntimeError("等待页面加载超时")

    def _evaluate(self, expression: str, sid: str) -> str:
        res = self._wait_for(self._send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True}, sid))
        return str((res.get("result") or {}).get("value") or "")

    def close(self):
        try:
            if self._target_id:
                self._send("Target.closeTarget", {"targetId": self._target_id})
        except Exception:
            pass
        finally:
            self.ws.close()


def fetch_rendered(url: str, port: int = DEFAULT_PORT, settle_ms: int = 2500,
                   timeout: float = 25.0) -> str:
    """在已登录的调试浏览器中打开 URL，返回渲染后的 HTML。

    浏览器未启动时抛 ``RuntimeError``（含可读原因），由调用方决定是否降级。
    """
    version = _http_json(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
    ws_url = version.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("浏览器未提供 CDP 调试地址")
    session = PageSession(ws_url, timeout=timeout)
    try:
        return session.open(url, settle_ms=settle_ms)
    finally:
        session.close()


if __name__ == "__main__":  # 自检：python -m src.core.cdp
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    if not is_debug_port_open(port):
        print(f"端口 {port} 未开启")
    else:
        try:
            cs = read_cookies(port)
            print(f"读到 {len(cs)} 条 Cookie")
        except RuntimeError as e:
            print(f"读取失败：{e}")

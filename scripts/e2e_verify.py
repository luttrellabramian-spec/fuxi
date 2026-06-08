#!/usr/bin/env python3
"""伏羲 端到端启动验证脚本

验证以下链路：
1. 端口连通性 — gRPC :50051 + HTTP :18789
2. HTTP /health 端点 — 报告 gRPC 目标
3. /tool/list 端点 — 返回至少 1 个工具
4. /tool/invoke 端点 — 调用 file_exists 工具
5. /chat 端点 — 一次完整对话（依赖 LLM 配置）

用法：
    # 验证已运行的服务
    python scripts/e2e_verify.py

    # 自动启动服务再验证
    python scripts/e2e_verify.py --start

    # 真实 LLM 对话（需 E2E_LIVE=1 + config/local.yaml）
    E2E_LIVE=1 python scripts/e2e_verify.py

    # 自定义端口
    python scripts/e2e_verify.py --http-port 18789 --grpc-port 50051

退出码：
    0  — 所有检查通过
    1  — 有检查失败
    2  — 启动失败

环境变量：
    E2E_LIVE   设 1 启用真实 LLM 对话测试（需 config/local.yaml）
    HTTP_PORT  默认 18789
    GRPC_PORT  默认 50051
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional, Tuple

# 默认端口
DEFAULT_HTTP_PORT = 18789
DEFAULT_GRPC_PORT = 50051

# 默认项目根（脚本相对位置）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


# ════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════


class Colors:
    """ANSI 颜色"""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    GRAY = "\033[90m"
    RESET = "\033[0m"


# ASCII 标识符（避免 Windows GBK 编码问题）
PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


def cprint(color: str, msg: str) -> None:
    """彩色打印"""
    if sys.stdout.isatty():
        print(f"{color}{msg}{Colors.RESET}")
    else:
        print(msg)


def check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """检查 TCP 端口是否在监听"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def http_get(url: str, timeout: float = 5.0, raw: bool = False) -> Tuple[int, Optional[Any]]:
    """HTTP GET，返回 (status_code, body) — raw=True 时 body 是 str"""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read()
            # 用 utf-8 严格模式解码；HTML 页面可能有非 utf-8 字节，fallback 到 latin-1
            try:
                body_str = raw_body.decode("utf-8")
            except UnicodeDecodeError:
                body_str = raw_body.decode("latin-1", errors="replace")
            if raw:
                return resp.status, body_str
            try:
                return resp.status, json.loads(body_str)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def http_post(
    url: str, payload: Dict[str, Any], timeout: float = 30.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """HTTP POST JSON，可选 extra_headers（如 Authorization）。"""
    try:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, None
    except Exception as exc:
        return 0, {"error": str(exc)}


def wait_for_port(host: str, port: int, max_wait: float = 30.0) -> bool:
    """等待端口就绪（间隔 0.5s）"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if check_port(host, port, timeout=0.5):
            return True
        time.sleep(0.5)
    return False


# ════════════════════════════════════════════════════════════════════
# 启动服务（--start 模式）
# ════════════════════════════════════════════════════════════════════


def start_services(http_port: int, grpc_port: int) -> bool:
    """在后台启动 Python gRPC + TypeScript 网关服务"""
    cprint(Colors.BLUE, "\n[启动] 编译并启动服务...")

    # 1) 编译 TypeScript（如果需要）
    typescript_dir = os.path.join(PROJECT_ROOT, "typescript")
    if not os.path.exists(os.path.join(typescript_dir, "dist", "gateway.js")):
        cprint(Colors.GRAY, "  [1/3] 编译 TypeScript 网关...")
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=typescript_dir, shell=True,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            cprint(Colors.RED, f"  TypeScript 编译失败:\n{result.stderr}")
            return False

    # 2) 启动 Python gRPC
    cprint(Colors.GRAY, "  [2/3] 启动 Python gRPC 服务...")
    python_dir = os.path.join(PROJECT_ROOT, "python")
    if sys.platform == "win32":
        venv_python = os.path.join(python_dir, "venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(python_dir, "venv", "bin", "python")
    if not os.path.exists(venv_python):
        venv_python = sys.executable  # fallback

    # 在 Linux/macOS 上直接 & 后台；Windows 上用 CREATE_NEW_PROCESS_DETACH
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    grpc_log = open(os.path.join(PROJECT_ROOT, "logs", "e2e_grpc.log"), "w") \
        if os.path.isdir(os.path.join(PROJECT_ROOT, "logs")) else subprocess.DEVNULL
    try:
        subprocess.Popen(
            [venv_python, "main.py"],
            cwd=python_dir,
            stdout=grpc_log, stderr=grpc_log,
            creationflags=creationflags,
        )
    except Exception as e:
        cprint(Colors.RED, f"  启动 gRPC 失败: {e}")
        return False

    if not wait_for_port("localhost", grpc_port, max_wait=15):
        cprint(Colors.RED, f"  gRPC port {grpc_port} not ready in 15s")
        return False
    cprint(Colors.GREEN, f"  [OK] gRPC listening on :{grpc_port}")

    # 3) 启动 TypeScript 网关
    cprint(Colors.GRAY, "  [3/3] starting TypeScript gateway...")
    gateway_log = open(os.path.join(PROJECT_ROOT, "logs", "e2e_gateway.log"), "w") \
        if os.path.isdir(os.path.join(PROJECT_ROOT, "logs")) else subprocess.DEVNULL
    try:
        subprocess.Popen(
            ["node", "dist/gateway.js"],
            cwd=typescript_dir,
            stdout=gateway_log, stderr=gateway_log,
            creationflags=creationflags,
        )
    except Exception as e:
        cprint(Colors.RED, f"  failed to start gateway: {e}")
        return False

    if not wait_for_port("localhost", http_port, max_wait=15):
        cprint(Colors.RED, f"  HTTP port {http_port} not ready in 15s")
        return False
    cprint(Colors.GREEN, f"  [OK] HTTP gateway listening on :{http_port}")
    return True


# ════════════════════════════════════════════════════════════════════
# 检查项
# ════════════════════════════════════════════════════════════════════


class Checker:
    """带累计统计的检查器"""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.results: list = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            status = PASS
            color = Colors.GREEN
        else:
            self.failed += 1
            status = FAIL
            color = Colors.RED
        cprint(color, f"  [{status}] {name}")
        if detail:
            cprint(Colors.GRAY, f"          {detail}")
        self.results.append((name, ok, detail))

    def skip(self, name: str, reason: str) -> None:
        self.skipped += 1
        cprint(Colors.YELLOW, f"  [{SKIP}] {name}  ({reason})")
        self.results.append((name, None, reason))

    def summary(self) -> int:
        total = self.passed + self.failed + self.skipped
        cprint(Colors.BLUE, f"\n{'='*60}")
        cprint(Colors.BLUE, f"  Total: {total}    Passed: {self.passed}    Failed: {self.failed}    Skipped: {self.skipped}")
        cprint(Colors.BLUE, f"{'='*60}")
        if self.failed == 0:
            cprint(Colors.GREEN, "\n  ALL PASSED! Fuxi engine is ready.")
            return 0
        else:
            cprint(Colors.RED, "\n  FAILED. Please investigate.")
            return 1


def run_checks(http_port: int, grpc_port: int) -> int:
    """执行所有检查"""
    c = Checker()
    base = f"http://localhost:{http_port}"
    # 提前加载 API key（用于 /tool/invoke 和 /chat 的鉴权）
    api_key = _load_yaml_api_key()

    cprint(Colors.BLUE, "\n[1/6] 检查端口连通性...")
    c.check(f"gRPC 端口 :{grpc_port} 监听", check_port("localhost", grpc_port))
    c.check(f"HTTP 端口 :{http_port} 监听", check_port("localhost", http_port))

    cprint(Colors.BLUE, "\n[2/6] /health 端点...")
    status, body = http_get(f"{base}/health")
    ok = status == 200 and body and body.get("ok") and body.get("alive")
    detail = f"status={status}, body={json.dumps(body) if body else 'None'}"
    c.check("返回 200 + ok + alive", ok, detail)
    grpc_target_ok = body and str(grpc_port) in str(body.get("grpcHost", ""))
    c.check(f"gRPC 目标指向 :{grpc_port}", grpc_target_ok,
            body.get("grpcHost") if body else "无响应")

    cprint(Colors.BLUE, "\n[3/6] /tool/list 端点...")
    status, body = http_get(f"{base}/tool/list")
    tools = (body or {}).get("data", {}).get("tools", [])
    c.check("返回 200", status == 200, f"status={status}")
    c.check("至少注册 5 个工具", len(tools) >= 5,
            f"实际 {len(tools)} 个工具: {[t.get('name') for t in tools[:5]]}")

    cprint(Colors.BLUE, "\n[4/6] /tool/invoke 端点...")
    # 找一个 L0 工具来调用 — file_exists 在 _BASE_DIR 之外会返回 false，不抛错
    target_tool = next((t["name"] for t in tools if t.get("name") == "file_exists"), None)
    if not target_tool:
        c.skip("调用 file_exists", "工具未注册")
    else:
        # 若有 config/local.yaml 里的 key，用它通过 gRPC 鉴权
        extra_headers = None
        if api_key:
            extra_headers = {"Authorization": f"Bearer {api_key}"}
        status, body = http_post(f"{base}/tool/invoke", {
            "tool_name": target_tool,
            "arguments": {"path": "C:/Windows/System32/cmd.exe"},  # 项目外，应返回 false 而非抛错
            "session_id": "e2e-verify",
        }, extra_headers=extra_headers)
        # 两种 OK 情况：1) 真有 auth 头并 result=False；2) 没 auth 头且 gRPC 鉴权关闭，result=False
        body = body or {}
        result_value = body.get("data", {}).get("result") if isinstance(body.get("data"), dict) else None
        ok = status == 200 and (body.get("ok") is True) and result_value is False
        c.check(f"调用 {target_tool} 返回 200 + result=false（安全）", ok,
                f"status={status}, ok={body.get('ok')}, "
                f"data={json.dumps(body.get('data'))[:200]}, "
                f"err={str(body.get('error', ''))[:80]}")

    cprint(Colors.BLUE, "\n[5/6] /chat/ui 端点（HTML）...")
    status, body = http_get(f"{base}/chat/ui", raw=True)
    c.check("/chat/ui 返回 200", status == 200, f"status={status}, size={len(body or '')}")

    cprint(Colors.BLUE, "\n[6/6] /settings/ui 端点（HTML）...")
    status, body = http_get(f"{base}/settings/ui", raw=True)
    c.check("/settings/ui 返回 200", status == 200, f"status={status}, size={len(body or '')}")

    cprint(Colors.BLUE, "\n[BONUS] /chat 端点（依赖 LLM）...")
    # 优先级：环境变量 E2E_LIVE=1 才真打 LLM，否则只验 schema
    live_mode = os.environ.get("E2E_LIVE", "0") == "1"

    if live_mode and api_key:
        # 真实 LLM 测试 — 用 Authorization 头注入 key
        status, body = http_post(
            f"{base}/chat",
            {
                "message": "用 5 个字以内回复 OK",
                "session_id": "e2e-verify-live",
            },
            timeout=60,
            extra_headers={"Authorization": f"Bearer {api_key}"},
        )
        content = str(body.get("data", {}).get("content", "")) if body else ""
        if status == 200 and body and body.get("ok") and "Authentication failed" not in content:
            c.check("/chat 真实 LLM 响应", True, f"content={content[:80]}")
        else:
            c.check("/chat 真实 LLM 响应", False, f"status={status}, content={content[:120]}")
    else:
        # Schema 验证（不依赖 LLM）
        status, body = http_post(f"{base}/chat", {
            "message": "回复 OK",
            "session_id": "e2e-verify",
        })
        if status == 200 and body and body.get("ok") is not None:
            c.skip("/chat 真实 LLM", "设 E2E_LIVE=1 + config/local.yaml 启用真实对话测试")
        else:
            c.check("/chat schema 响应", False, f"status={status}")

    return c.summary()


def _load_yaml_api_key() -> Optional[str]:
    """从 config/local.yaml 读取 API key（不打印 key 本身）。"""
    import yaml
    yaml_path = os.path.join(PROJECT_ROOT, "config", "local.yaml")
    if not os.path.exists(yaml_path):
        return None
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("llm", {}).get("api_key")
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="伏羲 端到端启动验证")
    parser.add_argument("--start", action="store_true", help="自动启动服务再验证")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help=f"HTTP 端口 (默认 {DEFAULT_HTTP_PORT})")
    parser.add_argument("--grpc-port", type=int, default=DEFAULT_GRPC_PORT, help=f"gRPC 端口 (默认 {DEFAULT_GRPC_PORT})")
    args = parser.parse_args()

    cprint(Colors.BLUE, "="*60)
    cprint(Colors.BLUE, "  伏羲 端到端启动验证")
    cprint(Colors.BLUE, "="*60)
    cprint(Colors.GRAY, f"  HTTP: http://localhost:{args.http_port}")
    cprint(Colors.GRAY, f"  gRPC: localhost:{args.grpc_port}")

    if args.start:
        if not start_services(args.http_port, args.grpc_port):
            cprint(Colors.RED, "\nstart failed")
            return 2

    return run_checks(args.http_port, args.grpc_port)


if __name__ == "__main__":
    sys.exit(main())

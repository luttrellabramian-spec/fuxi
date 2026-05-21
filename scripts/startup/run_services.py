"""Start Fuxi gRPC and HTTP gateway for local debugging."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
TYPESCRIPT_DIR = PROJECT_ROOT / "typescript"


def main() -> None:
    env = os.environ.copy()

    print("[1] 启动 gRPC 服务...")
    grpc_proc = subprocess.Popen(
        [sys.executable, "src/grpc_server.py"],
        cwd=PYTHON_DIR,
        env=env,
    )
    print(f"  gRPC PID: {grpc_proc.pid}")
    time.sleep(4)

    print("[2] 启动 Gateway...")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", 18789))
            print("  端口 18789 可用")
        except OSError:
            print("  端口 18789 已被占用")

    gateway_proc = subprocess.Popen(
        ["node", "dist/gateway.js"],
        cwd=TYPESCRIPT_DIR,
        env=env,
    )
    print(f"  Gateway PID: {gateway_proc.pid}")
    time.sleep(3)

    print("\n[3] 测试 API...")
    try:
        response = requests.get("http://localhost:18789/health", timeout=5)
        print(f"  Health: {response.json()}")
    except Exception as exc:
        print(f"  Health error: {exc}")

    print("\n服务运行中，按 Ctrl+C 停止")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        grpc_proc.terminate()
        gateway_proc.terminate()
        print("已停止")


if __name__ == "__main__":
    main()

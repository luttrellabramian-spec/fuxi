"""Start Fuxi services quietly for local smoke checks."""

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
TYPESCRIPT_DIR = PROJECT_ROOT / "typescript"


def main() -> None:
    env = os.environ.copy()

    grpc_proc = subprocess.Popen(
        [sys.executable, "src/grpc_server.py"],
        cwd=PYTHON_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"gRPC PID: {grpc_proc.pid}")
    time.sleep(5)

    gateway_proc = subprocess.Popen(
        ["node", "dist/gateway.js"],
        cwd=TYPESCRIPT_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Gateway PID: {gateway_proc.pid}")
    time.sleep(3)
    print("服务已启动")


if __name__ == "__main__":
    main()

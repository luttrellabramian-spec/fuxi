"""Start the gRPC server after reading LLM config from the environment."""

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"


def main() -> None:
    print("=== 启动 gRPC 服务（环境变量配置）===")
    print("请先设置 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 或使用 config/local.yaml。")

    proc = subprocess.Popen(
        [sys.executable, "src/grpc_server.py"],
        cwd=PYTHON_DIR,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(f"PID: {proc.pid}")
    time.sleep(3)
    if proc.poll() is not None:
        out, _ = proc.communicate()
        print(f"退出代码: {proc.returncode}")
        print(out[:1000])
    else:
        print("服务运行中")


if __name__ == "__main__":
    main()

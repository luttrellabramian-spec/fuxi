"""Start the gRPC server with environment-based LLM configuration."""

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"


def main() -> None:
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "src/grpc_server.py"],
        cwd=PYTHON_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(f"gRPC PID: {proc.pid}")
    time.sleep(3)
    if proc.poll() is not None:
        out, _ = proc.communicate()
        print(f"Exit: {proc.returncode}\n{out[:500]}")


if __name__ == "__main__":
    main()

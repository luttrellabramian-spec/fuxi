"""伏羲主入口"""
import os
import sys
from src.grpc_server import serve

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "50051"))
    print("Starting Fuxi Engine...")
    print("Press Ctrl+C to stop.")
    serve(port)

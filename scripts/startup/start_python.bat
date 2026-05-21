@echo off
cd /d "%~dp0\python"
echo Starting Python gRPC server...
python src\grpc_server.py
pause
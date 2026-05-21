@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"

echo.
echo ============================================================
echo   伏羲 V0.2.5 WIP - AI Agent 引擎
echo ============================================================
echo.

REM 从 config/local.yaml 或环境变量加载配置，不硬编码密钥
echo [配置] 正在加载配置...
if exist "%PROJECT_ROOT%\config\local.yaml" (
    echo [配置] 使用 config\local.yaml 配置
) else (
    echo [配置] 使用环境变量配置（若未设置，将使用 settings 页面手动配置）
)
echo.

echo [编译] TypeScript 代码...
cd "%PROJECT_ROOT%\typescript"
call npm run build
if errorlevel 1 (
    echo [错误] TypeScript 编译失败
    pause
    exit /b 1
)
echo [完成] 编译成功

echo.
echo [启动] 正在启动伏羲服务...
echo.

REM 清理可能占用端口的进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :50051 ^| findstr LISTENING') do taskkill /F /PID %%a 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :18789 ^| findstr LISTENING') do taskkill /F /PID %%a 2>nul

REM 等待端口释放
timeout /t 2 /nobreak >nul

REM 启动 Python gRPC 服务
echo [1/2] 启动 gRPC 引擎服务...
cd "%PROJECT_ROOT%\python"
start /b cmd /c "python src\grpc_server.py"

REM 等待 gRPC 服务启动
echo [等待] gRPC 服务启动中...
timeout /t 6 /nobreak >nul

REM 启动 TypeScript 网关（使用环境变量）
echo [2/2] 启动 HTTP 网关...
cd "%PROJECT_ROOT%\typescript"
start /b cmd /c "set LLM_API_KEY=%LLM_API_KEY% && set LLM_BASE_URL=%LLM_BASE_URL% && set LLM_MODEL=%LLM_MODEL% && node dist\gateway.js"

REM 等待网关启动
echo [等待] 网关启动中...
timeout /t 3 /nobreak >nul

echo.
echo ============================================================
echo   伏羲已启动！
echo ============================================================
echo.
echo   HTTP 网关:     http://localhost:18789
echo   设置页面:     http://localhost:18789/settings/ui
echo   健康检查:     http://localhost:18789/health
echo.
echo   正在打开对话页面...
echo ============================================================
echo.

REM 打开对话页面
start http://localhost:18789/chat/ui

pause

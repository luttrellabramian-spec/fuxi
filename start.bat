@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   伏羲 V0.2.5 WIP - AI Agent 引擎
echo ============================================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

REM 检查 Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 18+
    pause
    exit /b 1
)

REM 检查依赖
if not exist "python\venv" (
    echo [信息] 首次运行，正在安装 Python 依赖...
    cd python
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt -q
    cd ..
    echo [完成] Python 依赖安装完成
)

if not exist "typescript\node_modules" (
    echo [信息] 首次运行，正在安装 TypeScript 依赖...
    cd typescript
    call npm install --silent
    cd ..
    echo [完成] TypeScript 依赖安装完成
)

REM 检查配置
if not exist "config\local.yaml" (
    echo.
    echo [配置] 检测到未配置 API Key
    echo.
    echo 请选择配置方式：
    echo   1. 打开设置页面配置（推荐）
    echo   2. 命令行输入
    echo   3. 稍后配置
    echo.
    set /p choice="请输入选择 (1/2/3): "
    
    if "!choice!"=="2" (
        echo.
        set /p api_key="请输入 API Key: "
        set /p base_url="请输入 Base URL (如 https://api.deepseek.com/v1): "
        set /p model="请输入模型名称 (如 deepseek-chat): "
        
        (
        echo # 伏羲本地配置
        echo llm:
        echo   api_key: "!api_key!"
        echo   base_url: "!base_url!"
        echo   model: "!model!"
        ) > config\local.yaml
        
        echo.
        echo [完成] 配置已保存到 config\local.yaml
    )
)

echo.
echo [编译] 正在编译 TypeScript 网关...
cd typescript
call npm run build
if errorlevel 1 (
    echo [错误] TypeScript 编译失败，请查看上方日志
    pause
    exit /b 1
)
cd ..
echo [完成] TypeScript 编译完成

echo.
echo [启动] 正在启动伏羲服务...
echo.

REM 启动 Python gRPC 服务
echo [1/2] 启动 gRPC 引擎服务...
cd python
start /b cmd /c "venv\Scripts\activate.bat && python main.py"
cd ..

REM 等待 gRPC 服务启动
timeout /t 2 /nobreak >nul

REM 启动 TypeScript 网关
echo [2/2] 启动 HTTP 网关...
cd typescript
start /b cmd /c "node dist\gateway.js"
cd ..

REM 等待网关启动
timeout /t 3 /nobreak >nul

echo.
echo ============================================================
echo   伏羲已启动！
echo ============================================================
echo.
echo   HTTP 网关: http://localhost:18789
echo   设置页面: http://localhost:18789/settings/ui
echo   健康检查: http://localhost:18789/health
echo.
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.

REM 打开设置页面（如果需要）
if "!choice!"=="1" (
    start http://localhost:18789/settings/ui
)

REM 保持窗口打开
pause >nul

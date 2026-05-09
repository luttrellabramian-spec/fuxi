@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   伏羲 V0.1.0 - CLI 聊天模式
echo ============================================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
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
    echo 请先配置 API Key 才能使用
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
    echo [完成] 配置已保存
)

echo.
echo [启动] 正在启动伏羲服务...
echo.

REM 启动 Python gRPC 服务（后台）
echo [1/2] 启动 gRPC 引擎服务...
cd python
start /b cmd /c "venv\Scripts\activate.bat && python main.py"
cd ..

REM 等待 gRPC 服务启动
timeout /t 2 /nobreak >nul

REM 启动 TypeScript 网关（后台）
echo [2/2] 启动 HTTP 网关...
cd typescript
start /b cmd /c "npm start"
cd ..

REM 等待网关启动
timeout /t 3 /nobreak >nul

echo.
echo [完成] 服务已启动，正在进入聊天界面...
echo.

REM 启动 CLI 聊天
cd typescript
node dist/src/cli.js
cd ..

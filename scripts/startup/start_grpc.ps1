# 请通过环境变量或 config/local.yaml 配置 LLM API
Write-Host "请设置环境变量 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL (可选)"
Write-Host "示例:"
Write-Host '  $env:LLM_API_KEY = "your-api-key"'
Write-Host '  $env:LLM_BASE_URL = "https://api.openai.com/v1"'
Write-Host '  $env:LLM_MODEL = "gpt-4o"'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Start-Process python -ArgumentList 'src/grpc_server.py' -WorkingDirectory (Join-Path $projectRoot 'python') -WindowStyle Hidden -PassThru | Select-Object Id

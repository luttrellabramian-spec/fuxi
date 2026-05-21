param(
    [switch]$SkipNode,
    [switch]$SkipPython
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $SkipNode) {
    Push-Location (Join-Path $projectRoot "typescript")
    try {
        npm run build
    } finally {
        Pop-Location
    }
}

if (-not $SkipPython) {
    Push-Location (Join-Path $projectRoot "python")
    try {
        python -m pytest -q tests/test_hot_memory.py tests/test_warm_memory.py tests/test_tool_registry.py
    } finally {
        Pop-Location
    }
}

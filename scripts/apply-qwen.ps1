param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot -Parent
$targetRoot = (Resolve-Path -LiteralPath $Target).Path
if ($targetRoot -eq $sourceRoot) { throw 'Source and target must differ' }
$files = @('agent/core/model.py', 'agent/api/routes.py', 'agent/api/dashboard_routes.py',
    'agent/core/runtime.py', 'agent/core/streaming_runtime.py',
    'ui/src/components/ChatComposer.vue', 'scripts/check_qwen.py', 'README.sandbox.md',
    'agent/env_utils.py', 'agent/server.py', 'agent/core/task_intent.py',
    'agent/core/checkpoint_history.py', 'agent/core/middleware/message_sanitize.py')
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $file) -Destination (Join-Path $targetRoot $file) -Force
}
foreach ($file in @('.env', '.env.example')) {
    $sourceConfig = [IO.File]::ReadAllText((Join-Path $sourceRoot $file))
    $targetPath = Join-Path $targetRoot $file
    $targetConfig = [IO.File]::ReadAllText($targetPath)
    $targetConfig = [regex]::Replace($targetConfig, '(?m)^DEEPSEEK_(API_KEY|BASE_URL)=.*\r?\n?', '')
    foreach ($key in @('DASHSCOPE_API_KEY', 'QWEN_BASE_URL', 'QWEN_MODEL', 'MAIN_MODEL')) {
        $pattern = '(?m)^' + $key + '=[^\r\n]*'
        $line = [regex]::Match($sourceConfig, $pattern).Value
        if (-not $line) { throw "Missing source configuration: $key" }
        if ([regex]::IsMatch($targetConfig, $pattern)) {
            $targetConfig = [regex]::Replace($targetConfig, $pattern, $line)
        } else {
            $targetConfig = $targetConfig.TrimEnd() + "`n" + $line + "`n"
        }
    }
    [IO.File]::WriteAllText($targetPath, $targetConfig)
}
$env:PYTHONUTF8 = '1'
Push-Location $targetRoot
try {
    & '.\.venv\Scripts\python.exe' scripts/check_qwen.py
    if ($LASTEXITCODE -ne 0) { throw 'Qwen configuration verification failed' }
    & node ui/node_modules/vite/bin/vite.js build ui
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }

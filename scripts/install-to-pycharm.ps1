param([string]$Target = 'C:\Users\hello\PycharmProjects\aicoding')
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
$targetRoot = (Resolve-Path -LiteralPath $Target).Path
$expectedTarget = [IO.Path]::GetFullPath('C:\Users\hello\PycharmProjects\aicoding').TrimEnd('\')
if ($targetRoot.TrimEnd('\') -ne $expectedTarget) { throw 'Unexpected target directory' }
if ($sourceRoot -eq $targetRoot) { throw 'Source and target must differ' }
$folders = @('agent', 'scripts', 'ui', 'data', 'logs', 'ai_workspace')
foreach ($folder in $folders) {
    if (Test-Path -LiteralPath (Join-Path $targetRoot $folder)) {
        throw "Target already contains $folder; review existing content before installing"
    }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$oldVenv = Join-Path $targetRoot '.venv'
$backupVenv = Join-Path $targetRoot ('.venv.before-sandbox-' + $stamp)
if ([IO.Path]::GetFullPath($oldVenv) -ne (Join-Path $expectedTarget '.venv')) { throw 'Unexpected virtual environment path' }
if (-not ([IO.Path]::GetFullPath($backupVenv).StartsWith($expectedTarget + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'Backup is outside target' }
if (Test-Path -LiteralPath $oldVenv) {
    Move-Item -LiteralPath $oldVenv -Destination $backupVenv
}
$files = @('main.py', '.env', '.env.example', '.gitignore', 'README.md', 'README.sandbox.md',
    'requirements.txt', 'requirements.lock.txt', 'requirements-sandbox.txt',
    'start.cmd', 'check-sandbox.cmd', 'setup.ps1')
foreach ($file in $files) {
    $destination = Join-Path $targetRoot $file
    if (Test-Path -LiteralPath $destination) {
        Copy-Item -LiteralPath $destination -Destination ($destination + '.before-sandbox-' + $stamp + '.bak')
    }
    Copy-Item -LiteralPath (Join-Path $sourceRoot $file) -Destination $destination
}
foreach ($folder in ($folders + '.venv')) {
    & robocopy (Join-Path $sourceRoot $folder) (Join-Path $targetRoot $folder) /E /XD __pycache__ .vite /NJH /NJS /NFL /NDL /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copy failed: $folder" }
}
$pythonBase = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
foreach ($venv in @((Join-Path $targetRoot '.venv'), (Join-Path $targetRoot 'ai_workspace\runtimes\python\default\.venv'))) {
    & $pythonBase -m venv --without-pip $venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment refresh failed: $venv" }
    & (Join-Path $venv 'Scripts\python.exe') (Join-Path $targetRoot 'scripts\refresh_venv_entrypoints.py')
    if ($LASTEXITCODE -ne 0) { throw "Console launcher refresh failed: $venv" }
}
foreach ($file in @('.env', '.env.example')) {
    $path = Join-Path $targetRoot $file
    $content = [IO.File]::ReadAllText($path)
    foreach ($setting in @{
        AI_WORKSPACE_ROOT = (Join-Path $targetRoot 'ai_workspace')
        LOCAL_SHELL_WORKSPACE = (Join-Path $targetRoot 'ai_workspace')
        LX_AICODING_DATA_DIR = (Join-Path $targetRoot 'data')
        LX_AICODING_LOG_DIR = (Join-Path $targetRoot 'logs')
    }.GetEnumerator()) {
        $line = $setting.Key + '=' + $setting.Value.Replace('\', '/')
        $content = [regex]::Replace($content, '(?m)^' + $setting.Key + '=.*$', $line)
    }
    [IO.File]::WriteAllText($path, $content)
}
$env:PYTHONUTF8 = '1'
$projectPython = Join-Path $targetRoot '.venv\Scripts\python.exe'
& $projectPython (Join-Path $targetRoot 'scripts\check_sandbox.py')
if ($LASTEXITCODE -ne 0) { throw 'Sandbox verification failed' }
& $projectPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed' }
Write-Output ('Installed project: ' + $targetRoot)
Write-Output ('Original environment backup: ' + $backupVenv)

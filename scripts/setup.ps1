param([string]$PythonExe = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$workspaceRoot = Join-Path $projectRoot 'ai_workspace'
if (-not $PythonExe) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path -LiteralPath $candidate) { $PythonExe = $candidate }
    else { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
}
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable (exit $LASTEXITCODE)" }
}
Invoke-Checked $PythonExe @('-m', 'venv', (Join-Path $projectRoot '.venv'))
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$lockFile = Join-Path $projectRoot 'requirements.lock.txt'
if (-not (Test-Path -LiteralPath $lockFile)) { $lockFile = Join-Path $projectRoot 'requirements.txt' }
Invoke-Checked $projectPython @('-m', 'pip', 'install', '-r', $lockFile)
Invoke-Checked $PythonExe @('-m', 'venv', (Join-Path $workspaceRoot 'runtimes\python\default\.venv'))
$sandboxPython = Join-Path $workspaceRoot 'runtimes\python\default\.venv\Scripts\python.exe'
Invoke-Checked $sandboxPython @('-m', 'pip', 'install', '-r', (Join-Path $projectRoot 'requirements-sandbox.txt'))
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination (Join-Path $projectRoot '.env')
}
$envPath = Join-Path $projectRoot '.env'
$configuration = [IO.File]::ReadAllText($envPath)
foreach ($entry in @{
    AI_WORKSPACE_ROOT = $workspaceRoot
    LOCAL_SHELL_WORKSPACE = $workspaceRoot
    LX_AICODING_DATA_DIR = (Join-Path $projectRoot 'data')
    LX_AICODING_LOG_DIR = (Join-Path $projectRoot 'logs')
}.GetEnumerator()) {
    $line = $entry.Key + '=' + $entry.Value.Replace('\', '/')
    $configuration = [regex]::Replace($configuration, '(?m)^' + $entry.Key + '=.*$', $line)
}
[IO.File]::WriteAllText($envPath, $configuration)
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'ui\node_modules\vite\bin\vite.js'))) {
    Push-Location (Join-Path $projectRoot 'ui')
    try { Invoke-Checked 'npm.cmd' @('ci') } finally { Pop-Location }
}
$skillsTarget = Join-Path $workspaceRoot 'skills'
New-Item -ItemType Directory -Path $skillsTarget -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'agent\skills') | ForEach-Object {
    $destination = Join-Path $skillsTarget $_.Name
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $_.FullName -Destination $skillsTarget -Recurse
    }
}
Invoke-Checked $projectPython @((Join-Path $PSScriptRoot 'check_sandbox.py'))

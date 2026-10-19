param([string]$PythonExe = '')
$ErrorActionPreference = 'Stop'
$setupScript = Join-Path $PSScriptRoot 'scripts\setup.ps1'
if ($PythonExe) {
    & $setupScript -PythonExe $PythonExe
} else {
    & $setupScript
}

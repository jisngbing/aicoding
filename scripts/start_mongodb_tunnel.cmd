@echo off
setlocal
set "SSH_TARGET=%~1"
if not defined SSH_TARGET set "SSH_TARGET=%MONGODB_SSH_TARGET%"
if not defined SSH_TARGET (
  echo Usage: start_mongodb_tunnel.cmd user@server
  echo Or set MONGODB_SSH_TARGET in your shell environment.
  exit /b 2
)
echo MongoDB tunnel: 127.0.0.1:37017 to remote 127.0.0.1:27017
echo Enter the SSH password when prompted. Keep this window open while using aicoding.
ssh -N -L 127.0.0.1:37017:127.0.0.1:27017 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 "%SSH_TARGET%"
pause

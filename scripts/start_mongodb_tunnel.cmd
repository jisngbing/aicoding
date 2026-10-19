@echo off
echo MongoDB tunnel: 127.0.0.1:37017 to remote 127.0.0.1:27017
echo Enter the SSH password when prompted. Keep this window open while using aicoding.
ssh -N -L 127.0.0.1:37017:127.0.0.1:27017 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 root@8.160.114.12
pause

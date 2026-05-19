@echo off
title FlowForge v2 — Bridge
cd /d "%~dp0"
echo.
echo   ╔══════════════════════════════════════════╗
echo   ║        FlowForge v2 — Bridge           ║
echo   ╠══════════════════════════════════════════╣
echo   ║  Dashboard: http://127.0.0.1:5556       ║
echo   ║  Auto-auth: POST /api/auth/auto         ║
echo   ║  Presiona Ctrl+C para detener           ║
echo   ╚══════════════════════════════════════════╝
echo.
python run_bridge.py
pause

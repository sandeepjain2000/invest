@echo off
setlocal EnableExtensions
title Internship — Approve (Admin, Auto)
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo ============================================================
echo   AUTO — Approver: publish internship (no keypress pauses)
echo ============================================================

%PY% internship_pipeline.py approve --auto %*

endlocal

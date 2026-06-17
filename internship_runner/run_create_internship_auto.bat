@echo off
setlocal EnableExtensions
title Internship — Create (Employer, Auto)
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo ============================================================
echo   AUTO — Employer: create internship (no keypress pauses)
echo ============================================================

%PY% internship_pipeline.py create --auto %*

endlocal

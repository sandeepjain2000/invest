@echo off
setlocal EnableExtensions
title Internship — Apply (Student, Auto)
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo ============================================================
echo   AUTO — Student: apply to internship (no keypress pauses)
echo ============================================================

%PY% internship_pipeline.py apply --auto %*

endlocal

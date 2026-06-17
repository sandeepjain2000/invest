@echo off
setlocal EnableExtensions
title Internship — Select (Recruiter, Auto)
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo ============================================================
echo   AUTO — Selector: pick candidate (no keypress pauses)
echo ============================================================

%PY% internship_pipeline.py select --auto %*

endlocal

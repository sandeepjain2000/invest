@echo off
setlocal EnableExtensions
title Internship — Full Demo (Auto)
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo ============================================================
echo   AUTO DEMO — all four roles, continuous with timed pauses
echo     1. Employer  — create
echo     2. Approver  — publish
echo     3. Student   — apply
echo     4. Selector  — select
echo.
echo   Pauses: internship_config.json -^> auto_run section
echo ============================================================

%PY% internship_pipeline.py run-all --auto %*
%PY% internship_pipeline.py export-voice

endlocal

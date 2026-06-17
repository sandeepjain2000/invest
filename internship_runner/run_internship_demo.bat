@echo off
setlocal EnableExtensions
title Internship — Full Demo (All Roles)
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo ============================================================
echo   FULL DEMO — all four roles in sequence:
echo     1. Employer  — create internship
echo     2. Approver  — publish internship
echo     3. Student   — apply
echo     4. Selector  — select candidate
echo.
echo   Install voice: pip install -r requirements.txt
echo   Transcripts: voice\transcripts\
echo   Audio:       voice\audio\
echo ============================================================
echo.
pause

%PY% internship_pipeline.py run-all %*

echo.
echo Export transcripts for voice-over:
%PY% internship_pipeline.py export-voice

echo.
pause
endlocal

@echo off

setlocal EnableExtensions

title Internship — Create (Employer)

cd /d "%~dp0"



set "PY=python"

where py >nul 2>&1 && set "PY=py -3"



echo ============================================================

echo   ROLE: Employer — Create internship posting

echo   Voice: Edge neural TTS (or transcript for voice-over)

echo ============================================================

echo.

pause



%PY% internship_pipeline.py create %*



echo.

pause

endlocal


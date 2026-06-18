@echo off

setlocal EnableExtensions

title Internship — Apply (Student)

cd /d "%~dp0"



set "PY=python"

where py >nul 2>&1 && set "PY=py -3"



echo ============================================================

echo   ROLE: Student — Apply to open internship

echo ============================================================

echo.

pause



%PY% internship_pipeline.py apply %*



echo.

pause

endlocal


@echo off

setlocal EnableExtensions

title Internship — Select (Recruiter)

cd /d "%~dp0"



set "PY=python"

where py >nul 2>&1 && set "PY=py -3"



echo ============================================================

echo   ROLE: Selector — Choose candidate for internship

echo ============================================================

echo.

pause



%PY% internship_pipeline.py select %*



echo.

pause

endlocal


@echo off

setlocal EnableExtensions

title Internship — Approve (Admin)

cd /d "%~dp0"



set "PY=python"

where py >nul 2>&1 && set "PY=py -3"



echo ============================================================

echo   ROLE: Approver — Publish internship for applications

echo ============================================================

echo.

pause



%PY% internship_pipeline.py approve %*



echo.

pause

endlocal


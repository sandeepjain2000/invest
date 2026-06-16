@echo off
setlocal EnableExtensions

cd /d "%~dp0"

rem Defaults live in sender_config.json:
rem   emails_per_run         = 16
rem   max_companies_per_run  = 50
rem   max_queries_per_run    = 20
rem Optional overrides below (leave blank to use JSON defaults).

set "SEND_LIMIT="
set "MAX_COMPANIES="
set "MAX_QUERIES="
set "BROWSER=auto"
set "REGION=India"

echo ============================================================
echo   Partnership pipeline — scrape then send
echo   Folder: %CD%
echo.
echo   Step 1: Scrape CA Connect + Google (see RUN SUMMARY for details)
echo   Step 2: Send emails via Brevo (see RUN SUMMARY for per-industry counts)
echo.
echo   Settings: sender_config.json  ^|  browser=%BROWSER%  region=%REGION%
echo ============================================================
echo.
pause

set "RUN_ARGS=run --browser %BROWSER% --region %REGION%"
if not "%SEND_LIMIT%"=="" set "RUN_ARGS=%RUN_ARGS% --send-limit %SEND_LIMIT%"
if not "%MAX_COMPANIES%"=="" set "RUN_ARGS=%RUN_ARGS% --max-companies %MAX_COMPANIES%"
if not "%MAX_QUERIES%"=="" set "RUN_ARGS=%RUN_ARGS% --max-queries %MAX_QUERIES%"

python immigration_pipeline.py %RUN_ARGS%

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
    echo   Finished OK ^(exit code 0^)
) else (
    echo   Finished with errors ^(exit code %EXIT_CODE%^)
)
echo   See RUN SUMMARY above for this run's counts.
echo   Logs: %CD%\logs\
echo ============================================================
echo.
pause

endlocal
exit /b %EXIT_CODE%

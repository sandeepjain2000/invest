@echo off
setlocal EnableExtensions

cd /d "%~dp0\.."

rem --- Override ICAI send count (blank = sender_config.json ca_bulk_emails_per_run, default 50) ---
rem --- Not the same as emails_per_run (32) used by partnership scrape/send scripts ---
set "SEND_LIMIT="

echo ============================================================
echo   CA email send only — caconnect.icai.org contacts
echo   Folder: %CD%
echo   Source: data\db\ca_bulk.db
echo   Default: 50 emails per run from ICAI ca_bulk.db
echo   ^(Partnership scrape/send uses emails_per_run=32 in sender_config.json^)
echo.
echo   No scrape — harvest CAs with scripts\harvest_ca_from_icai.bat first.
echo ============================================================
echo.
pause

set "RUN_ARGS=send-ca"
if not "%SEND_LIMIT%"=="" set "RUN_ARGS=%RUN_ARGS% --limit %SEND_LIMIT%"

python immigration_pipeline.py %RUN_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
    echo   Finished OK ^(exit code 0^)
) else (
    echo   Finished with errors ^(exit code %EXIT_CODE%^)
)
echo   Status:  python ca_bulk_import.py status
echo   Logs:    %CD%\logs\
echo ============================================================
echo.
pause

endlocal
exit /b %EXIT_CODE%

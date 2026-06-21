@echo off
setlocal EnableExtensions

cd /d "%~dp0\.."

rem --- Override send count (blank = config\sender_config.json ca_bulk_emails_per_run, default 10) ---
set "SEND_LIMIT="

echo ============================================================
echo   CA email send only — caconnect.icai.org contacts
echo   Folder: %CD%
echo   Source: data\db\ca_bulk.db
echo   Default: 10 emails per run (funding intro template)
echo.
echo   No scrape — harvest CAs with scripts\run_ca_bulk_import.bat first.
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

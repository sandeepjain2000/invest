@echo off

setlocal EnableExtensions



cd /d "%~dp0"



rem Tunables are in sender_config.json:

rem   emails_per_run         = 2   (send target)

rem   max_companies_per_run  = 50  (keep scraping past sites with no email)

rem   max_queries_per_run    = 20  (Google search rounds cap)

rem Optional CLI overrides below (leave blank to use JSON defaults).



set "MAX_COMPANIES="

set "MAX_QUERIES="

set "BROWSER=auto"

set "REGION=India"



echo ============================================================

echo   Partnership pipeline — full end-to-end run

echo   Folder: %CD%

echo.

echo   Step 1: Scrape up to 50 companies ^(stops early once 2 have email^)

echo   Step 2: Check inboxes and forward human replies

echo   Step 3: Send up to 2 partnership emails

echo.

echo   Settings: sender_config.json  ^|  browser=%BROWSER%  region=%REGION%

echo ============================================================

echo.

pause



set "RUN_ARGS=run --browser %BROWSER% --region %REGION%"

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


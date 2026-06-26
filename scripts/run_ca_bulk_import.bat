@echo off
rem Renamed for clarity - forwards to harvest_ca_from_icai.bat
echo NOTE: Use scripts\harvest_ca_from_icai.bat  (this old name still works)
echo.
call "%~dp0harvest_ca_from_icai.bat" %*
exit /b %ERRORLEVEL%

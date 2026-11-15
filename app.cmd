@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 (
  echo Could not enter the project directory.
  pause
  exit /b 1
)
where pwsh.exe >nul 2>nul
if errorlevel 1 goto windowsps
pwsh.exe -NoLogo -NoProfile -File "%~dp0scripts\open-frontend.ps1" %*
goto finished
:windowsps
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\open-frontend.ps1" %*
:finished
set "result=%errorlevel%"
if not "%result%"=="0" (
  echo.
  echo Frontend launcher did not complete. Read the message above.
  echo No process was stopped and no retry was made.
  pause
)
popd
exit /b %result%

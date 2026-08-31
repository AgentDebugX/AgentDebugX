@echo off
setlocal

rem Clear an inherited PSModulePath before delegating. A value exported by
rem PowerShell 7 can stop Windows PowerShell 5.1 from finding its own bundled
rem modules, so let the child process derive the variable for itself.
set "PSModulePath="

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
  -File "%~dp0agentdebug-hook.ps1" ^
  -Platform "%~1"

rem Capture never fails a host session.
exit /b 0

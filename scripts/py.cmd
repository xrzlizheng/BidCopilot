@echo off
setlocal
rem BidCopilot dev python launcher.
rem ????????????????? venv trampoline spawn ????os error 5??
rem ????????????? venv ? site-packages ?? PYTHONPATH?
rem ???scripts\py.cmd -m pytest tests\unit -q
set "BACKEND=%~dp0..\backend"
for /f "tokens=2 delims==" %%a in ('findstr /b "home" "%BACKEND%\.venv\pyvenv.cfg"') do set "PYHOME=%%a"
set "PYHOME=%PYHOME: =%"
set "PYTHONPATH=%BACKEND%;%BACKEND%\.venv\Lib\site-packages"
set "PYTHONIOENCODING=utf-8"
"%PYHOME%\python.exe" %*

@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" goto :project_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto :python_launcher
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto :system_python
echo Python 3.11+ was not found. Install Python or restore the project's .venv folder.
goto :failed

:project_python
"%~dp0.venv\Scripts\python.exe" "%~dp0bootstrap.py" %*
goto :result

:python_launcher
py -3 "%~dp0bootstrap.py" %*
goto :result

:system_python
python "%~dp0bootstrap.py" %*

:result
if errorlevel 1 goto :failed
exit /b 0

:failed
echo Startup failed. See the error above. Application logs are saved in data\logs when available.
pause
exit /b 1

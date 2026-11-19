@echo off
rem Build dist\FormCastLite.exe on Windows. Needs Python 3.10+ on PATH (the py launcher or python).
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

if not exist .venv-build (
  %PY% -m venv .venv-build || goto :fail
)
call .venv-build\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt -r requirements-build.txt || goto :fail
python build.py %* || goto :fail

echo.
echo Done: %~dp0dist\FormCastLite.exe
exit /b 0

:fail
echo Build failed.
exit /b 1

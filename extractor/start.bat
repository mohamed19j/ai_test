@echo off
rem Demarre le service d'extraction Python sur http://127.0.0.1:8000
cd /d %~dp0
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
goto :eof

:error
echo Installation des dependances Python echouee.
exit /b 1

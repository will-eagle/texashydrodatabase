@echo off
setlocal
cd /d "%~dp0"

set "PY=%~dp0Python\embed\python.exe"

echo === Validating submissions ===
"%PY%" ".\Python\scripts\validate.py"
if errorlevel 1 (
    echo.
    echo Validation reported failures. Check submissions\rejected\ for details.
    pause
    exit /b 1
)
echo.
pause

echo === Ingesting validated submissions ===
"%PY%" ".\Python\scripts\ingest_submission.py"
if errorlevel 1 (
    echo.
    echo Ingest failed. DB rolled back per-submission; see error above.
    pause
    exit /b 1
)
echo.
pause
endlocal
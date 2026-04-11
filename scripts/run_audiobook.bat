@echo off
REM ===========================================================================
REM  run_audiobook.bat — drag-and-drop launcher for the Chatterbox audiobook
REM  generator. Designed for non-technical Windows users.
REM
REM  Usage:
REM    1) Drag a PDF file onto this .bat file in Explorer, OR
REM    2) Double-click this file and paste a PDF path when prompted.
REM
REM  Prerequisites (one-time):
REM    * Run scripts\setup_chatterbox_windows.bat first. That installs Python
REM      packages and downloads the Chatterbox model weights.
REM    * NVIDIA GPU with recent drivers. See
REM      scripts\chatterbox_windows_quickstart.md for troubleshooting.
REM ===========================================================================

setlocal EnableDelayedExpansion

REM Resolve the repo root = parent of the scripts\ directory this file lives in.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

echo.
echo  AudiobookMaker - Chatterbox Finnish audiobook generator
echo  =======================================================
echo.

REM ---------------------------------------------------------------------------
REM  Step 1: locate the venv Python.
REM ---------------------------------------------------------------------------
set "VENV_PY=%REPO_ROOT%\.venv-chatterbox\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo  ERROR: Chatterbox venv not found at
    echo    %VENV_PY%
    echo.
    echo  You need to run setup first. Double-click:
    echo    scripts\setup_chatterbox_windows.bat
    echo.
    echo  After setup finishes, run this file again.
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM  Step 2: determine the PDF path.
REM  Priority: first command-line arg, else prompt the user.
REM ---------------------------------------------------------------------------
set "PDF_PATH=%~1"

if "%PDF_PATH%"=="" (
    echo  Drag a PDF file onto this window and press Enter,
    echo  or type the full path to your PDF:
    echo.
    set /p "PDF_PATH=PDF path: "
)

REM Strip surrounding quotes if the user dragged a path with spaces.
set "PDF_PATH=%PDF_PATH:"=%"

if "%PDF_PATH%"=="" (
    echo.
    echo  ERROR: No PDF path given. Exiting.
    pause
    exit /b 1
)

if not exist "%PDF_PATH%" (
    echo.
    echo  ERROR: File not found:
    echo    %PDF_PATH%
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM  Step 3: run the generator.
REM ---------------------------------------------------------------------------
echo.
echo  PDF:     %PDF_PATH%
echo  Output:  %REPO_ROOT%\dist\audiobook\
echo  Device:  auto (CUDA if available, else CPU)
echo.
echo  Synthesis is about to start. On an RTX 3080 Ti this takes
echo  ~80-110 minutes for a 180-page PDF. The script resumes
echo  automatically if you stop it with Ctrl-C.
echo.
echo  Press Ctrl-C to cancel, or
pause

"%VENV_PY%" -u "%REPO_ROOT%\scripts\generate_chatterbox_audiobook.py" ^
    --pdf "%PDF_PATH%" ^
    --out "%REPO_ROOT%\dist\audiobook" ^
    --device auto

set "RC=%ERRORLEVEL%"

if %RC% NEQ 0 (
    echo.
    echo  Generator exited with error code %RC%.
    echo  See the output above for details.
    pause
    exit /b %RC%
)

REM ---------------------------------------------------------------------------
REM  Step 4: open the output folder in Explorer so the user finds the MP3.
REM ---------------------------------------------------------------------------
echo.
echo  DONE. Opening the output folder...
echo.
start "" "%REPO_ROOT%\dist\audiobook"

pause
exit /b 0

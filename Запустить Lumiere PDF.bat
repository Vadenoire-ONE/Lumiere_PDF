@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  Lumiere PDF — автоматический запуск
rem  - создаёт виртуальное окружение .venv (при первом запуске)
rem  - устанавливает зависимости из requirements.txt
rem  - запускает lumiere_pdf.py
rem ============================================================

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "STAMP=%VENV_DIR%\.deps_installed"

rem --- Проверяем наличие Python ---
where py >nul 2>&1
if %errorlevel%==0 (
    set "PY_LAUNCHER=py -3"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PY_LAUNCHER=python"
    ) else (
        echo [ОШИБКА] Python не найден. Установите Python 3 с https://www.python.org/
        pause
        exit /b 1
    )
)

rem --- Создаём venv при первом запуске ---
if not exist "%PYTHON_EXE%" (
    echo [Lumiere PDF] Создаю виртуальное окружение...
    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
)

rem --- Устанавливаем зависимости (один раз / при изменении requirements.txt) ---
set "NEED_INSTALL=0"
if not exist "%STAMP%" set "NEED_INSTALL=1"
if exist "%STAMP%" (
    for %%I in (requirements.txt) do set "REQ_DATE=%%~tI"
    for %%I in ("%STAMP%")        do set "STAMP_DATE=%%~tI"
    if not "%REQ_DATE%"=="%STAMP_DATE%" set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="1" (
    echo [Lumiere PDF] Устанавливаю зависимости...
    "%PYTHON_EXE%" -m pip install --upgrade pip >nul
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось установить зависимости.
        pause
        exit /b 1
    )
    > "%STAMP%" echo ok
)

rem --- Запуск приложения без чёрного окна ---
start "" "%VENV_DIR%\Scripts\pythonw.exe" "lumiere_pdf.py"

endlocal
exit /b 0

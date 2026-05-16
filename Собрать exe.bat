@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  Lumiere PDF — сборка портативного .exe через PyInstaller
rem  Результат: dist\LumierePDF.exe (один файл, без зависимостей)
rem ============================================================

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

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
    echo [Build] Создаю виртуальное окружение...
    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
)

echo [Build] Устанавливаю зависимости и PyInstaller...
"%PYTHON_EXE%" -m pip install --upgrade pip >nul
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
"%PYTHON_EXE%" -m pip install pyinstaller
if errorlevel 1 goto :fail

rem --- Очистка предыдущих сборок ---
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

echo [Build] Запускаю PyInstaller...
"%PYTHON_EXE%" -m PyInstaller --clean --noconfirm lumiere_pdf.spec
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Готово! Портативный файл:
echo    %CD%\dist\LumierePDF.exe
echo  Скопируйте его на любую Windows-машину и запускайте.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo [ОШИБКА] Сборка завершилась неудачно.
pause
exit /b 1

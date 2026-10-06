@echo off
chcp 65001 >nul
title Dark Carnival - запуск из исходников
cd /d "%~dp0"

echo ======================================================================
echo   DARK CARNIVAL - полный режим (с распознаванием экрана)
echo ======================================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден.
    echo Установите Python 3.12 с python.org и ОБЯЗАТЕЛЬНО отметьте
    echo галочку "Add python.exe to PATH" при установке.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создаю виртуальное окружение... это делается один раз.
    python -m venv .venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать окружение.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Виртуальное окружение уже есть.
)

echo [2/3] Проверяю библиотеки. Первый запуск качает около 2 ГБ - наберитесь терпения.
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ОШИБКА] Установка библиотек не удалась. Проверьте интернет.
    pause
    exit /b 1
)

echo [3/3] Запускаю программу.
echo.
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Программа завершилась с ошибкой. Текст выше - отправьте его разработчику.
    pause
)

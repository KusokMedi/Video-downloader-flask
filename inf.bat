@echo off
chcp 65001 >nul
cls
:loop
echo.
echo      ╔════════════════════════════════════════════╗
echo      ║           Programm has Started             ║
echo      ╚════════════════════════════════════════════╝
echo.
python main.py
echo.
echo      ╔════════════════════════════════════════════╗
echo      ║     Programm has stopped. Restarting...    ║
echo      ╚════════════════════════════════════════════╝
echo.
goto loop

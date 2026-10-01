@echo off
cd /d %~dp0
REM Live MT5 chart + analyser, but every order is SIMULATED (paper). Safe for practice / forward testing.
REM KEEP-ALIVE: if SuperChart stops (crash, MT5 hiccup) it restarts by itself after 5 s and switches back
REM ON what was ON (trading / AUTO / NEWS) - only after an unexpected stop of less than 10 minutes.
REM Close THIS window to really stop it. Errors go to crash_log.txt.
title REYGUN SuperChart (paper)
python superchart.py --mode live --symbol XAUUSD_ph --paper
:loop
echo.
echo [%date% %time%] SuperChart stopped. Restarting in 5 s... (close this window to stop)
echo [%date% %time%] SuperChart stopped - auto restart>> crash_log.txt
timeout /t 5 /nobreak >nul
python superchart.py --mode live --symbol XAUUSD_ph --paper --resume --no-window
goto loop

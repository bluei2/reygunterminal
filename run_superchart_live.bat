@echo off
cd /d %~dp0
REM REAL orders: only after AUTO / NEWS / BUY-SELL is switched on with its confirm.
REM KEEP-ALIVE: if SuperChart stops (crash, MT5 hiccup) it restarts by itself after 5 s and switches back
REM ON what was ON (trading / AUTO / NEWS) - only after an unexpected stop of less than 10 minutes.
REM Close THIS window to really stop it. Errors go to crash_log.txt.
title REYGUN SuperChart (live)
python superchart.py --mode live --symbol XAUUSD_ph
:loop
echo.
echo [%date% %time%] SuperChart stopped. Restarting in 5 s... (close this window to stop)
echo [%date% %time%] SuperChart stopped - auto restart>> crash_log.txt
timeout /t 5 /nobreak >nul
python superchart.py --mode live --symbol XAUUSD_ph --resume --no-window
goto loop

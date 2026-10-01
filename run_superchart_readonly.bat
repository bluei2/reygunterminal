@echo off
cd /d %~dp0
REM Chart + analyser only. The trade panel is hidden, nothing can be sent.
python superchart.py --mode live --symbol XAUUSD_ph --no-trading
pause

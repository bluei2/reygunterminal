@echo off
cd /d %~dp0
REM MT5 must be open and logged in on this VPS. Enable: Tools > Options > Expert Advisors > Allow algorithmic trading
python reygun_term.py --mode live --symbol XAUUSD_ph --chart-tf M5 --record
pause

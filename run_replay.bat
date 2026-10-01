@echo off
cd /d %~dp0
REM Usage: drag a recordings\*.csv file onto this .bat, or: run_replay.bat recordings\XAUUSD_....csv
python reygun_term.py --mode replay --file "%~1" --speed 20
pause

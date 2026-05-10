@echo off
setlocal

set PYTHON=.\etl-env\Scripts\python.exe
set SCRIPT=scripts\run_backtest_report.py

set UNIVERSE=--universe all
set FROM=--from 2018-01-01
set TO=--to 2022-12-31
set THRESHOLD=--threshold 0.1
set BENCHMARK=--benchmark SPY

%PYTHON% %SCRIPT% %UNIVERSE% %FROM% %TO% %THRESHOLD% %BENCHMARK% %*

endlocal

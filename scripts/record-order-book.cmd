@echo off
rem Daily order-book recording (Task Scheduler, weekdays 09:10). Runs from the main checkout,
rem whose config holds the read-only market-data token; code comes from this worktree.
cd /d "%~dp0..\..\TradePilot-GitHub"
set PYTHONPATH=%~dp0..
.venv\Scripts\python.exe -u "%~dp0record-order-book.py" data\orderbook >> data\orderbook\recorder.log 2>&1

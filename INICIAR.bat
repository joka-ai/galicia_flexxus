@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
cd backend
set SERVER_MODE=false
set PORT=5000
start http://localhost:5000
python app.py
pause

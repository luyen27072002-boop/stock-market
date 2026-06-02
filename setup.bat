@echo off
cd /d %~dp0
python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt
echo.
echo Xong. Chay run_app.bat de mo app.
pause

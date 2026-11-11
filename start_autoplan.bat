@echo off
REM Starts the AutoPlan app and the Telegram bot in two windows.
REM Put a shortcut to this file in shell:startup to run it when the computer turns on.
cd /d "%~dp0"
start "AutoPlan app" cmd /k ".venv\Scripts\activate && streamlit run app.py --server.headless true"
start "AutoPlan bot" cmd /k ".venv\Scripts\activate && python telegram_bot.py"

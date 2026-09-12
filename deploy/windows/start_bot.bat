@echo off
REM Arranque del bot OPTCG (TeamVictus) en Windows.
REM Se ejecuta automáticamente al iniciar sesión (tarea programada) o manualmente con doble clic.
cd /d "%~dp0"
venv\Scripts\python.exe bot.py

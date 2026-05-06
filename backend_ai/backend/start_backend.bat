@echo off
title CNTx labs Safe Starter
set PATH=%PATH%;C:\Users\Editor1\AppData\Local\Python\pythoncore-3.14-64\
call .venv\Scripts\activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
pause
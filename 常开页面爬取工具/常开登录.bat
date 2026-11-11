@echo off
chcp 65001 >nul
title 常开登录
:: ---- 找 Python（换电脑后安装路径可能不同：逐个候选找，最后回落到 PATH）----
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYEXE%" for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
if not exist "%PYEXE%" for /d %%d in ("%ProgramFiles%\Python3*") do if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
if not exist "%PYEXE%" if exist "C:\Python312\python.exe" set "PYEXE=C:\Python312\python.exe"
if not exist "%PYEXE%" for /f "delims=" %%p in ('where python 2^>nul') do set "PYEXE=%%p"
if not exist "%PYEXE%" (
  echo [错误] 没找到 Python。请先安装 Python 3.12（安装时勾选 Add Python to PATH），再重试。
  echo         下载地址：https://www.python.org/downloads/
  pause
  exit /b 1
)
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"
"%PYEXE%" "%~dp0常开登录.py"
pause

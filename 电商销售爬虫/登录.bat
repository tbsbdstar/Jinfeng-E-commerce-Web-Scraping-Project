@echo off
chcp 65001 >nul
title 重新登录
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
set PY="%PYEXE%"
:menu
cls
echo ==================================
echo         重新登录（登录态失效时用）
echo ==================================
echo   1^) 全部三家（小红书 / 抖音 / 拼多多）
echo   2^) 小红书
echo   3^) 抖音
echo   4^) 拼多多
echo   5^) 淘宝（开调试Chrome，手动过滑块登录）
echo   0^) 退出
echo.
set /p ch=请输入编号后回车: 
if "%ch%"=="1" ( %PY% "%~dp0login.py" xhs dy pdd & goto done )
if "%ch%"=="2" ( %PY% "%~dp0login.py" xhs & goto done )
if "%ch%"=="3" ( %PY% "%~dp0login.py" dy & goto done )
if "%ch%"=="4" ( %PY% "%~dp0login.py" pdd & goto done )
if "%ch%"=="5" ( call "%~dp0淘宝Chrome.bat" & goto done )
if "%ch%"=="0" exit
goto menu
:done
echo.
echo 完成。登录成功后，下次抓取会自动清除该平台的失效标记。
pause

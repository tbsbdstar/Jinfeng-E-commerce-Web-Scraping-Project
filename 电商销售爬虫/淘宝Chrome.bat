@echo off
chcp 65001 >nul
title Taobao Chrome (login for crawler)
:: ---- 找 Chrome（不同电脑装的位置可能不一样）----
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo [Error] Chrome not found. Please install Google Chrome first.
  pause
  exit /b 1
)
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%~dp0browser_profiles\淘宝_cdp" "https://loginmyseller.taobao.com/?from=taobaoindex&f=top&style=&sub=true&redirect_url=https%3A%2F%2Fmyseller.taobao.com%2F"
echo.
echo Taobao login Chrome opened (debug mode). Please login (pass the slider).
echo The Taobao crawler will connect to this Chrome automatically.
echo You can close this window; keep the Chrome for crawling.
pause

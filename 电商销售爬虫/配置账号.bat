@echo off
chcp 65001 >nul
title Configure Accounts
powershell -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0credentials_setup.ps1"

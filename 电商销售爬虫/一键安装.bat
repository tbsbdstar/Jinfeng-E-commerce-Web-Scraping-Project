@echo off
chcp 65001 >nul
title Ecom Sales Crawler - Installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

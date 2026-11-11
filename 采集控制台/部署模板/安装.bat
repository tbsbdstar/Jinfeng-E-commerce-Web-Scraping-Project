@echo off
chcp 65001 >nul
title 多平台电商经营数据自动采集与统计分析系统 V1.1 - 安装
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

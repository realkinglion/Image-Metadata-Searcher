@echo off
title 画像メタ情報検索くん v5.2 DnD Final
echo 画像メタ情報検索くん v5.2 DnD Finalを起動しています...
echo.

python main.py

if %errorlevel% neq 0 (
    echo.
    echo ==========================================
    echo エラーが発生しました。
    echo.
    echo 考えられる原因：
    echo - Python または必要なライブラリが不足
    echo - install_v5.bat を実行していない
    echo - Python PATH設定の問題
    echo - tkinterdnd2のインストール失敗
    echo.
    echo install_v5.bat を実行してください。
    echo ==========================================
    pause
)
@echo off
echo 画像メタ情報検索くん v5.2 DnD Final セットアップ
echo ==========================================
echo 必要なライブラリをインストールします...
echo.

pip install --upgrade pip

echo requirements.txtから依存関係をインストールしています...
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo requirements.txtでの一括インストールに失敗しました。
    echo 個別インストールを試行します...
    echo.
    
    echo Pillow-SIMDをインストールしています...
    pip install Pillow-SIMD>=9.0.0
    if %errorlevel% neq 0 (
        echo Pillow-SIMDのインストールに失敗しました。
        echo 通常のPillowをインストールします...
        pip install Pillow>=9.0.0
    )
    
    echo ExifReadをインストールしています...
    pip install exifread>=3.0.0
    
    echo Watchdogをインストールしています...
    pip install watchdog>=2.1.0
    
    echo tkinterdnd2をインストールしています...
    pip install tkinterdnd2>=0.3.0
)

echo.
echo ==========================================
echo インストール完了！
echo 今後は run_search_app_v5.bat で起動できます。
echo ==========================================
pause
@echo off
echo �摜���^��񌟍����� v5.2 DnD Final �Z�b�g�A�b�v
echo ==========================================
echo �K�v�ȃ��C�u�������C���X�g�[�����܂�...
echo.

pip install --upgrade pip

echo requirements.txt����ˑ��֌W���C���X�g�[�����Ă��܂�...
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo requirements.txt�ł̈ꊇ�C���X�g�[���Ɏ��s���܂����B
    echo �ʃC���X�g�[�������s���܂�...
    echo.
    
    echo Pillow-SIMD���C���X�g�[�����Ă��܂�...
    pip install Pillow-SIMD>=9.0.0
    if %errorlevel% neq 0 (
        echo Pillow-SIMD�̃C���X�g�[���Ɏ��s���܂����B
        echo �ʏ��Pillow���C���X�g�[�����܂�...
        pip install Pillow>=9.0.0
    )
    
    echo ExifRead���C���X�g�[�����Ă��܂�...
    pip install exifread>=3.0.0
    
    echo Watchdog���C���X�g�[�����Ă��܂�...
    pip install watchdog>=2.1.0
    
    echo tkinterdnd2���C���X�g�[�����Ă��܂�...
    pip install tkinterdnd2>=0.3.0
)

echo.
echo ==========================================
echo �C���X�g�[�������I
echo ����� run_search_app_v5.bat �ŋN���ł��܂��B
echo ==========================================
pause
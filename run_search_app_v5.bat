@echo off
title �摜���^��񌟍����� v5.2 DnD Final
echo �摜���^��񌟍����� v5.2 DnD Final���N�����Ă��܂�...
echo.

python main.py

if %errorlevel% neq 0 (
    echo.
    echo ==========================================
    echo �G���[���������܂����B
    echo.
    echo �l�����錴���F
    echo - Python �܂��͕K�v�ȃ��C�u�������s��
    echo - install_v5.bat �����s���Ă��Ȃ�
    echo - Python PATH�ݒ�̖��
    echo - tkinterdnd2�̃C���X�g�[�����s
    echo.
    echo install_v5.bat �����s���Ă��������B
    echo ==========================================
    pause
)
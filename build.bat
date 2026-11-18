@echo off
rem PyRemote .exe dosyalarini uretir. Cift tiklayarak calistirin.
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python bulunamadi. python.org adresinden Python 3.10+ kurun ve "Add to PATH" secenegini isaretleyin. & pause & exit /b 1)
python -m venv .build-venv || goto :hata
call .build-venv\Scripts\activate.bat
python -m pip install --upgrade pip || goto :hata
pip install -r requirements.txt pyinstaller || goto :hata
pyinstaller --noconfirm --clean --onefile --noconsole --name PyRemoteHost run_host.py || goto :hata
pyinstaller --noconfirm --clean --onefile --noconsole --name PyRemoteViewer run_viewer.py || goto :hata
pyinstaller --noconfirm --clean --onefile --console --name PyRemoteRelay run_relay.py || goto :hata
echo.
echo Tamamlandi. Dosyalar "dist" klasorunde:
dir /b dist\*.exe
pause
exit /b 0
:hata
echo.
echo Derleme basarisiz oldu. Yukaridaki hata iletisine bakin.
pause
exit /b 1

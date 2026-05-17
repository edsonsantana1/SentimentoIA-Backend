@echo off
REM ========================================
REM Script de Inicialização Segura - Windows
REM Aplica política asyncio ANTES do Uvicorn
REM ========================================

echo [INFO] Limpando ambiente Python anterior...
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak

echo [INFO] Ativando ambiente virtual...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERRO] Ambiente virtual não encontrado em venv\Scripts\activate.bat
    echo Crie com: python -m venv venv
    exit /b 1
)

echo [INFO] Instalando dependências...
pip install -q -r requirements.txt

echo.
echo [INFO] ========================================
echo [INFO] Iniciando API SentimentoIA (Windows Safe)
echo [INFO] Política: WindowsSelectorEventLoopPolicy
echo [INFO] Modo: Produção (sem --reload)
echo [INFO] ========================================
echo.

REM Sem --reload para evitar perda de política no subprocess
REM Usar --workers 1 para simplicidade em dev
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

pause

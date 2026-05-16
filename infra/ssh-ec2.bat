@echo off
setlocal

set REGION=us-east-1
set ENV=staging
set KEY=%TEMP%\fintel-k3s.pem
set LOCAL_PORT=9999

echo =^> Finding current k3s instance...
for /f "delims=" %%i in ('aws ec2 describe-instances --filters "Name=tag:Name,Values=fintel-k3s-%ENV%" "Name=instance-state-name,Values=running,pending" --region %REGION% --query "Reservations[0].Instances[0].InstanceId" --output text 2^>^&1') do set INSTANCE_ID=%%i

if "%INSTANCE_ID%"=="None" ( echo [error] No running instance found & exit /b 1 )
if "%INSTANCE_ID%"=="" ( echo [error] No running instance found & exit /b 1 )
echo   Instance: %INSTANCE_ID%

echo =^> Fetching SSH key from Secrets Manager...
if exist "%KEY%" ( icacls "%KEY%" /grant:r "%USERNAME%:F" >nul & del /f "%KEY%" )
set PYTHONUTF8=
"%~dp0..\etl-env\Scripts\python.exe" -c "import boto3; sm=boto3.client('secretsmanager',region_name='%REGION%'); r=sm.get_secret_value(SecretId='fintel/k3s-ssh-key-%ENV%')['SecretString']; open(r'%KEY%','w',newline='\n').write(r if r.endswith('\n') else r+'\n')"
if %ERRORLEVEL% neq 0 ( echo [error] Failed to fetch SSH key & exit /b 1 )
icacls "%KEY%" /inheritance:r /grant:r "%USERNAME%:R" >nul
echo   Key saved to %KEY%

echo =^> Opening EIC tunnel on localhost:%LOCAL_PORT%...
echo   (leave this window open — Ctrl+C to close tunnel)
echo.
start "EIC Tunnel" cmd /c "aws ec2-instance-connect open-tunnel --instance-id %INSTANCE_ID% --remote-port 22 --local-port %LOCAL_PORT% --region %REGION%"

echo =^> Waiting for tunnel to be ready...
timeout /t 3 /nobreak >nul

echo =^> Connecting via SSH...
ssh -i "%KEY%" -o StrictHostKeyChecking=no -p %LOCAL_PORT% ubuntu@localhost

endlocal

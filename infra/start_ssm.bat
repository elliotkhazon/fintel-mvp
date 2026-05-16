@echo off
setlocal

set REGION=us-east-1
set ENV=%1
if "%ENV%"=="" set ENV=staging

echo =^=^> Getting k3s instance ID (%ENV%)...
for /f "delims=" %%i in ('terraform -chdir=infra output -raw k3s_instance_id') do set INSTANCE_ID=%%i

if "%INSTANCE_ID%"=="" (
    echo [error] Could not get k3s_instance_id from terraform output
    exit /b 1
)

echo =^=^> Starting SSM session to %INSTANCE_ID% (%ENV%)
aws ssm start-session --target %INSTANCE_ID% --region %REGION%

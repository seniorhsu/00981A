@echo off
schtasks /create /tn "00981A_Download" /tr "\"C:\Users\senior\AppData\Local\Programs\Python\Python313\python.exe\" \"C:\ClaudeCode\download_00981A.py\"" /sc DAILY /st 16:30 /du 0002:30 /ri 15 /f
if %errorlevel% == 0 (
    echo 排程建立成功！
    schtasks /query /tn "00981A_Download" /fo LIST
) else (
    echo 排程建立失敗
)
pause

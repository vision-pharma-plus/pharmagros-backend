@echo off
echo Loading environment variables from .env...

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" (
        if not "%%A:~0,1%"=="#" (
            set "%%A=%%B"
        )
    )
)

echo Environment variables loaded.

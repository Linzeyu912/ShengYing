$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$goServer = Join-Path $projectRoot '.tooling\shengying-server.exe'
$stdout = Join-Path $projectRoot 'outputs\react-go-worker.stdout.log'
$stderr = Join-Path $projectRoot 'outputs\react-go-worker.stderr.log'

$listener = Get-NetTCPConnection -State Listen -LocalPort 8318 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if ($owner.CommandLine -notmatch 'uvicorn server\.model_worker:app') {
        throw "Port 8318 is already used by another process (PID $($listener.OwningProcess)); it was left untouched."
    }
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'outputs') | Out-Null
    $worker = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','server.model_worker:app','--host','127.0.0.1','--port','8318') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $ready = $false
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if ($worker.HasExited) { throw "Local worker exited early. See $stderr" }
        try {
            $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8318/healthz' -TimeoutSec 2
            $ready = $true
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "Local model worker did not start in time. See $stderr" }
}

Set-Location $projectRoot
& $goServer

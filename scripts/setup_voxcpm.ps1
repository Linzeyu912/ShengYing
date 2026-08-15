$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
$CompatiblePython = $null
$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    $Registered = & py -0p 2>$null
    foreach ($Line in $Registered) {
        if ($Line -match "-V:3\.(11|12)\s+\*?\s*(.+)$") {
            $Candidate = $Matches[2].Trim()
            if (Test-Path -LiteralPath $Candidate) {
                $CompatiblePython = $Candidate
                break
            }
        }
    }
}

if (-not $CompatiblePython) {
    $SystemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($SystemPython) {
        $Version = & $SystemPython.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ([version]$Version -ge [version]"3.10" -and [version]$Version -lt [version]"3.13") {
            $CompatiblePython = $SystemPython.Source
        }
    }
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$RebuildVenv = -not (Test-Path -LiteralPath $VenvPython)
if (-not $RebuildVenv) {
    $VenvVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $RebuildVenv = [version]$VenvVersion -lt [version]"3.10" -or [version]$VenvVersion -ge [version]"3.13"
}
if ($RebuildVenv -and -not $CompatiblePython -and -not $UvCommand) {
    throw "VoxCPM2 requires Python 3.10-3.12. Install Python 3.11, or install uv and run this script again."
}

if ($RebuildVenv) {
    if ($UvCommand) {
        if (-not $CompatiblePython) {
            Write-Host "Installing a managed Python 3.11 with uv..."
            & $UvCommand.Source python install 3.11
            $PythonSelector = "3.11"
        } else {
            $PythonSelector = $CompatiblePython
        }
        Write-Host "Creating a Python 3.11/3.12 virtual environment..."
        & $UvCommand.Source venv --python $PythonSelector --clear .venv
    } else {
        if (Test-Path -LiteralPath $VenvPython) {
            $VenvPath = (Resolve-Path -LiteralPath ".venv").Path
            if ((Split-Path -Parent $VenvPath) -ne $ProjectRoot) {
                throw "Refusing to rebuild unexpected venv path: $VenvPath"
            }
            Remove-Item -LiteralPath $VenvPath -Recurse -Force
        }
        & $CompatiblePython -m venv .venv
    }
}

function Install-Packages {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($UvCommand) {
        & $UvCommand.Source pip install --python $VenvPython @Arguments
    } else {
        & $VenvPython -m pip install @Arguments
    }
}

Install-Packages "-r" "requirements.txt"

$HasNvidia = $false
try {
    $HasNvidia = [bool](Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" })
} catch {
    $HasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
}

if ($HasNvidia) {
    Write-Host "NVIDIA GPU detected; installing matching CUDA 12.8 PyTorch packages..."
    Install-Packages "torch==2.9.1" "torchaudio==2.9.1" "--index-url" "https://download.pytorch.org/whl/cu128"
} else {
    Write-Host "No NVIDIA GPU detected; installing CPU PyTorch packages..."
    Install-Packages "torch==2.9.1" "torchaudio==2.9.1" "--index-url" "https://download.pytorch.org/whl/cpu"
}

Install-Packages "voxcpm==2.0.3" "modelscope"

if (-not (Test-Path "models\VoxCPM2\model.safetensors")) {
    Write-Host "Downloading VoxCPM2 from ModelScope (several GB)..."
    & $VenvPython -c "from modelscope import snapshot_download; snapshot_download('OpenBMB/VoxCPM2', local_dir='models/VoxCPM2')"
}

Write-Host ""
Write-Host "Setup complete. Run the launcher BAT and open http://127.0.0.1:8317/"

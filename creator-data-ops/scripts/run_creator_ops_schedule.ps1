param(
    [string]$Mode = "all",
    [string]$Platform = "",
    [string]$Account = ""
)

$ErrorActionPreference = "Stop"

function Get-PythonCommand {
    $preferred = @(
        "C:\Users\surface\anaconda3\python.exe"
    )

    foreach ($candidate in $preferred) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $fallback = @("python", "py")
    foreach ($candidate in $fallback) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            return $candidate
        }
    }

    throw "Python not found. Install Python first, then make sure python or py works in terminal."
}

$python = Get-PythonCommand
$bridgeScript = Join-Path $PSScriptRoot "creator_ops_bridge.py"

if ($Mode -eq "all") {
    & $python $bridgeScript fetch-all-platforms
    exit $LASTEXITCODE
}

if ($Mode -eq "platform") {
    if (-not $Platform) {
        throw "Platform is required for platform mode."
    }
    & $python $bridgeScript fetch-platform --platform $Platform
    exit $LASTEXITCODE
}

if ($Mode -eq "account") {
    if (-not $Platform -or -not $Account) {
        throw "Platform and Account are required for account mode."
    }
    & $python $bridgeScript manual-fetch --platform $Platform --account $Account
    exit $LASTEXITCODE
}

throw "Unsupported schedule mode: $Mode"

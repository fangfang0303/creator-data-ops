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

    $fallback = @(
        "python",
        "py"
    )

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

Write-Host "Running all-platform creator data fetch..."
& $python $bridgeScript fetch-all-platforms

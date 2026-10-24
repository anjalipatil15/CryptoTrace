# Runs the whole project from scratch: download both cases, calibrate on Nomad, freeze the
# numbers, analyze both cases, and open the presentations. About 45-60 minutes, mostly waiting
# on Etherscan. Safe to re-run: downloads are cached, so a re-run skips what is already done.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# --- setup -------------------------------------------------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating the Python environment..." -ForegroundColor Cyan
    py -3 -m venv .venv      # needs Python 3.10 or newer
    if ($LASTEXITCODE -ne 0) { throw "Could not create the environment. Install Python 3.10+ from python.org first." }
}
$py = ".venv\Scripts\python.exe"
& $py -m pip install -q -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not (Test-Path ".env") -or -not (Select-String -Path ".env" -Pattern "^ETHERSCAN_API_KEY=\S+" -Quiet)) {
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
    throw "Put your Etherscan API key in .env (the line ETHERSCAN_API_KEY=...) and run this script again."
}

$ct = ".venv\Scripts\cryptotrace.exe"
function Step($title, [string[]]$cmdArgs) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
    & $ct @cmdArgs
    if ($LASTEXITCODE -ne 0) { throw "'cryptotrace $($cmdArgs -join ' ')' failed. Fix the error above and run the script again." }
}

# --- the pipeline --------------------------------------------------------------------------
Step "1/5  Download Harmony (~5 min)"                    @("trace", "config/cases/harmony.yaml")
Step "2/5  Download Nomad (~15-30 min)"                  @("trace", "config/cases/nomad.yaml")
Step "3/5  Calibrate on Nomad and freeze (~15-20 min)"   @("calibrate", "config/cases/nomad.yaml", "--freeze")
Step "4/5  Analyze Nomad"                                @("analyze", "config/cases/nomad.yaml")
Step "5/5  Analyze Harmony with the frozen numbers"      @("analyze", "config/cases/harmony.yaml")

Write-Host ""
Write-Host "Done. Results: outputs\harmony\ and outputs\nomad\ (report.md has the numbers for docs\RESULTS.md)." -ForegroundColor Green
Start-Process "outputs\harmony\presentation.html"
Start-Process "outputs\nomad\presentation.html"

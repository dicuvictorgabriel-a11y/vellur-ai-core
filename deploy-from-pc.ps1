# deploy-from-pc.ps1 — run on YOUR PC (Windows PowerShell). Needs: git, gh (logged in), OpenSSH (built into Windows).
# Copies this folder to the VPS and runs the installer there interactively (it asks for your sudo password
# and for confirmation before anything optional, such as Docker or swap).
#
#   powershell -ExecutionPolicy Bypass -File deploy-from-pc.ps1
param(
  [string]$Server = "dicuvictorgabriel@185.4.113.26",
  [string]$Repo   = "dicuvictorgabriel-a11y/ring-city-render",
  [string]$Branch = "claude/vellur-ai-core-phase-1-jwjlrb"
)
$ErrorActionPreference = "Stop"
$tmp = Join-Path $env:TEMP ("vellur-ai-core-" + (Get-Date -Format "yyyyMMddHHmmss"))
Write-Host "1/3 Downloading $Repo@$Branch ..."
gh repo clone $Repo $tmp -- --depth 1 --branch $Branch --config core.autocrlf=false
if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
Write-Host "2/3 Copying to $Server ..."
ssh $Server "rm -rf ~/vellur-ai-core-src"
scp -r -q (Join-Path $tmp "vellur-ai-core") "${Server}:~/vellur-ai-core-src"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
Write-Host "3/3 Running the installer on the VPS (interactive) ..."
ssh -t $Server "bash ~/vellur-ai-core-src/install.sh"
Remove-Item -Recurse -Force $tmp

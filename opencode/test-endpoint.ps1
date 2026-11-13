# Checks the VELLUR AI gateway from your PC (Windows PowerShell). Uses $env:VELLUR_AI_KEY.
#   $env:VELLUR_AI_KEY = "sk-vellur-..."   ; .\test-endpoint.ps1
param([string]$Base = "https://ai.vellurdigital.com/v1")
if (-not $env:VELLUR_AI_KEY) { throw "Set `$env:VELLUR_AI_KEY first (sudo vac keys add <name> on the VPS)." }
$h = @{ Authorization = "Bearer $($env:VELLUR_AI_KEY)" }
$m = Invoke-RestMethod -Uri "$Base/models" -Headers $h
Write-Host "Models:" ($m.data.id -join ", ")
if (-not $m.data) { Write-Host "No inference backend connected yet (expected until the GPU is activated)."; exit 0 }
$body = @{ model = $m.data[0].id; max_tokens = 64; messages = @(@{ role = "user"; content = "Write a Python one-liner that reverses a string." }) } | ConvertTo-Json -Depth 5
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = Invoke-RestMethod -Uri "$Base/chat/completions" -Method Post -Headers $h -ContentType "application/json" -Body $body -TimeoutSec 900
Write-Host ("Answer in {0:n1}s:" -f $sw.Elapsed.TotalSeconds) $r.choices[0].message.content

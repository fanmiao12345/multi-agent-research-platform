param([Parameter(Mandatory=$true)][double]$MaxCostPerTask)
$ErrorActionPreference="Stop"
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
& .\.venv\Scripts\python.exe -m eval.web_baseline --max-cost $MaxCostPerTask
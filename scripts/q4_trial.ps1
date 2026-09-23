param([string]$Command="status", [string]$Task="", [string]$Result="", [double]$InterventionMinutes=0, [double]$RevisionMinutes=0, [string]$Notes="", [ValidateSet("plan","diagnostic")][string]$Kind="plan")
$ErrorActionPreference="Stop"
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
if($Command -eq "status") { & .\.venv\Scripts\python.exe -m eval.trial_log status; exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m eval.trial_log add --task $Task --result $Result --intervention-minutes $InterventionMinutes --revision-minutes $RevisionMinutes --notes $Notes --kind $Kind
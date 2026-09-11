param([Parameter(Mandatory=$true)][double]$MaxCost, [switch]$Grade)
$ErrorActionPreference="Stop"
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$out="eval/reports/q2_real_batch"
$argsList=@("-m","eval.business_eval","--mode","real","--repeats","3","--fault-rounds","2","--max-cost",$MaxCost,"--out",$out)
if($Grade){$argsList+="--grade"}
& .\.venv\Scripts\python.exe @argsList
& .\.venv\Scripts\python.exe -m eval.human_scores consolidate --report "$out/business_report.json" --workspace "$out/workspace" --out "eval/reports/q2_human_workbench"
Write-Host "请填写 eval/reports/q2_human_workbench/combined_scores.csv 后再执行 scripts/q2_ingest.ps1"
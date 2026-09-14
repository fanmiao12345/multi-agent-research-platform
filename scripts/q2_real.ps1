param([Parameter(Mandatory=$true)][double]$MaxCost, [double]$BatchMaxCost = 0, [switch]$Grade)
$ErrorActionPreference="Stop"
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$out="eval/reports/q2_real_batch"
$argsList=@("-m","eval.business_eval","--mode","real","--repeats","3","--fault-rounds","2","--max-cost",$MaxCost,"--out",$out)
# O-09：--max-cost 是单任务上限；整批花费必须另给累计上限（默认取单任务上限×3，可由 -BatchMaxCost 覆盖）
if($BatchMaxCost -le 0){$BatchMaxCost=$MaxCost*3}
$argsList+=@("--batch-max-cost",$BatchMaxCost)
if($Grade){$argsList+="--grade"}
Write-Host "单任务上限=$MaxCost 美元；整批累计上限=$BatchMaxCost 美元；超出后剩余尝试记 not_executed"
& .\.venv\Scripts\python.exe @argsList
& .\.venv\Scripts\python.exe -m eval.human_scores consolidate --report "$out/business_report.json" --workspace "$out/workspace" --out "eval/reports/q2_human_workbench"
Write-Host "请填写 eval/reports/q2_human_workbench/combined_scores.csv 后再执行 scripts/q2_ingest.ps1"

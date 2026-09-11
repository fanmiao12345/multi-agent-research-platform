param([string]$Scores="eval/reports/q2_human_workbench", [string]$Report="eval/reports/q2_real_batch/business_report.json")
$ErrorActionPreference="Stop"
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
& .\.venv\Scripts\python.exe -m eval.human_scores ingest --sheets $Scores --report $Report --out eval/reports/q2_real_batch/business_report_human.json
& .\.venv\Scripts\python.exe -m eval.q2_summary --business eval/reports/q2_real_batch/business_report.json --web eval/reports/q2_web_baseline.json --human eval/reports/q2_real_batch/business_report_human.json
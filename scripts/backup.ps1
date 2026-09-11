param([string]$Workspace = "workspaces", [Parameter(Mandatory=$true)][string]$Out)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .\.venv\Scripts\python.exe -m src.ops.backup --workspace $Workspace --out $Out
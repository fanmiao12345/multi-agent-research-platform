param([Parameter(Mandatory=$true)][string]$BackupDir, [string]$Workspace = "workspaces", [switch]$Confirm)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$arguments = @("-m", "src.ops.restore_cli", "--backup", $BackupDir, "--workspace", $Workspace)
if ($Confirm) { $arguments += "--confirm" }
& .\.venv\Scripts\python.exe @arguments
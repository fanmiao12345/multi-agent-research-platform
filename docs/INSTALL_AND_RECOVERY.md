# 安装、启动、备份与恢复

## 安装

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

脚本只操作项目目录：创建 `.venv`、按 `requirements.lock.txt` 安装依赖并运行健康检查。真实模型密钥放入项目根 `.env`，不得写入脚本或文档。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_workbench.ps1 --port 8765
```

CLI 示例：

```powershell
.venv\Scripts\python -m src.interfaces.cli "整理材料并写报告" --flow research --workspace workspaces --import-file .\资料.md
```

## 诊断

```powershell
.venv\Scripts\python -m src.ops.health
.venv\Scripts\python -m src.ops.verify
```

## 备份

先停止正在写入的工作台，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1 -Out .\backups
```

备份包含状态库在线快照、jobs、sessions 和 runs。`state.sqlite` 会执行可读性校验。

## 恢复

恢复会覆盖目标工作区数据，默认拒绝覆盖已有状态库；确认停机后显式加 `-Confirm`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\restore.ps1 -BackupDir .\backups\backup_YYYYMMDD_HHMMSS -Workspace workspaces -Confirm
```

恢复前应核对 `manifest.json` 和备份来源。不要对正在运行的工作区执行恢复。

## 数据迁移

- `state.sqlite` schema 由程序保守迁移；未知高版本会拒绝打开。
- `jobs/`、`sessions/` 的正文和历史产物使用文件布局，不做原地协议转换。
- 升级前先备份；恢复失败时保留原工作区并按 manifest 人工核对。
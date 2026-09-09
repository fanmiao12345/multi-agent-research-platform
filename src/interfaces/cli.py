"""统一任务入口的命令行客户端。默认Mock；不在命令行接收密钥。"""
import argparse
import json
import sys

from src.application.request import TaskRequest
from src.application.research import ResearchApplication, resume_research_job
from src.harness.models.factory import ModelConfigError


def _print_result(result, *, workspace=None) -> int:
    from pathlib import Path
    from config.settings import Settings
    payload = {"root_job_id": result.root_job_id, "run_id": getattr(result, "run_id", None),
               "termination_reason": result.termination_reason,
               "final_text": result.final_text}
    if getattr(result, "draft_level", None):
        payload["draft_level"] = result.draft_level
        payload["message"] = getattr(result, "message", "")
    root = Path(workspace) if workspace else Settings().workspace_dir
    index_path = root / "jobs" / (result.root_job_id or "") / "sources.json"
    if index_path.exists():
        try:
            records = json.loads(index_path.read_text(encoding="utf-8")).get("sources", [])
        except Exception:
            records = []
        counts: dict[str, int] = {}
        for record in records:
            counts[record.get("status", "?")] = counts.get(record.get("status", "?"), 0) + 1
        payload["sources"] = {"total": len(records),
                              "usable": counts.get("ok", 0) + counts.get("partial", 0),
                              "statuses": counts}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.termination_reason == "success" else 1


def main():
    parser = argparse.ArgumentParser(description="运行任务并保存根任务账本")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--profile")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--max-cost", type=float)
    parser.add_argument("--workspace")
    parser.add_argument("--import-file", action="append", default=[],
                        help="本地 TXT/Markdown 资料文件路径，可重复；只读原文")
    parser.add_argument("--import-text", action="append", default=[],
                        help="粘贴文本资料，可重复")
    parser.add_argument("--import-url", action="append", default=[],
                        help="用户指定的 http(s) 网页链接，可重复；按默认安全策略抓取正文")
    parser.add_argument("--allow-network", action="store_true",
                        help="声明允许联网研究（当前搜索未配置时无实际效果）")
    parser.add_argument("--flow", choices=("agent", "research"), default="agent",
                        help="agent=通用Agent循环（默认）；research=资料整理/研究写作链（需资料）")
    parser.add_argument("--resume-job",
                        help="对指定研究写作任务续跑（按检查点跳过已完成阶段，需 --workspace）")
    args = vars(parser.parse_args())
    workspace = args.pop("workspace")
    resume_job = args.pop("resume_job")
    task = args.pop("task")
    try:
        if resume_job:
            if not workspace:
                parser.error("--resume-job 需要 --workspace 指向任务所在工作区")
            result = resume_research_job(workspace_root=workspace, job_id=resume_job)
            raise SystemExit(_print_result(result, workspace=workspace))
        args["texts"] = tuple(args.pop("import_text"))
        args["files"] = tuple(args.pop("import_file"))
        args["urls"] = tuple(args.pop("import_url"))
        if task is None:
            parser.error("需要提供任务文本（或使用 --resume-job）")
        try:
            request = TaskRequest(task=task, **args)
        except ValueError as e:
            parser.error(str(e))
        app = ResearchApplication(request, workspace_root=workspace)
        result = app.run()
        raise SystemExit(_print_result(result, workspace=workspace))
    except ModelConfigError as e:
        parser.error(str(e))
    except ValueError as e:
        print(json.dumps({"status": "failed", "error_type": type(e).__name__,
                          "message": str(e)}, ensure_ascii=False))
        raise SystemExit(1) from None
    except Exception as e:
        print(json.dumps({"status": "failed", "error_type": type(e).__name__,
                          "message": str(e)}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

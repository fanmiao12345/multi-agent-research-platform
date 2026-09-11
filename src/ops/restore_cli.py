# -*- coding: utf-8 -*-
"""D10-01 恢复命令入口。"""
import argparse
import json

from config.settings import Settings
from src.ops.restore import restore_backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    result = restore_backup(
        args.backup,
        args.workspace or Settings().workspace_dir,
        confirm=args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
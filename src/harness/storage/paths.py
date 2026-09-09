# -*- coding: utf-8 -*-
"""
harness/storage/paths.py —— 路径边界（S2-08 本地部分）

原则：
- 项目内所有"按相对名访问 job 内文件"的入口都先经过 canonical + 包含性检查；
- 符号链接 / Windows junction 通过 realpath 解析到最终位置后再判断，链接逃逸必然越界；
- 写操作只发生在规范化后的受控目录内；对不存在路径同样校验其父链，防止先写后知。

本模块不做权限升级：读取用户明确给出的本地资料路径由调用方授权，
写入/覆盖一律限定在受控根目录（job 目录、artifacts 目录等）。
"""
from __future__ import annotations

import os
from pathlib import Path


class PathBoundaryError(ValueError):
    """路径越界或不受控访问，禁止继续。"""


def canonical(path: str | Path) -> Path:
    """绝对化并解析符号链接/junction；返回最终物理路径。"""
    return Path(os.path.realpath(os.path.abspath(os.fspath(path))))


def _resolve_through_links(path: str | Path) -> Path:
    """对存在与不存在路径都可靠的规范化解法。

    os.path.realpath 在 Windows 上对"最终段还不存在"的路径不会展开中间的
    junction/符号链接，直接做包含性检查会被逃逸。这里先找到最长已存在前缀
    并解析它，再把不存在的尾部原样接回。
    """
    p = Path(os.path.abspath(os.fspath(path)))
    head, tail = p, []
    while not head.exists():
        tail.append(head.name)
        parent = head.parent
        if parent == head:
            break
        head = parent
    resolved = Path(os.path.realpath(os.fspath(head)))
    for name in reversed(tail):
        resolved = resolved / name
    return resolved


def is_under(child: Path, root: Path) -> bool:
    """规范化后的 child 是否仍位于规范化后的 root 之内（child != root）。"""
    try:
        child.relative_to(root)
    except ValueError:
        return False
    return child != root


def ensure_under(child: Path, root: Path) -> Path:
    """child 必须严格位于 root 之内，否则抛出 PathBoundaryError。"""
    root = _resolve_through_links(root)
    child = _resolve_through_links(child)
    if not is_under(child, root):
        raise PathBoundaryError(
            f"路径越界或不受控：{child} 不在受控目录 {root} 内")
    return child


def resolve_under(root: str | Path, relative_or_absolute: str | Path) -> Path:
    """把相对名（或看似绝对、含 ../ 的输入）解析为 root 内的规范路径。

    - 允许调用方传入绝对路径，只要规范化后仍在 root 内（如 artifacts/<name>）；
    - 越界、指向不存在父链的逃逸路径都会抛 PathBoundaryError；
    - 返回的路径已 mkdir 父目录之后即可安全写入。
    """
    root_path = _resolve_through_links(root)
    target = Path(os.fspath(relative_or_absolute))
    full = target if target.is_absolute() else root_path / target
    return ensure_under(full, root_path)


def ensure_relative_name(name: str) -> str:
    """索引/接口处的文件名白名单：只允许单段、无分隔符的名称。"""
    if not isinstance(name, str) or not name:
        raise PathBoundaryError("名称不能为空")
    if Path(name).name != name:
        raise PathBoundaryError(f"名称不能包含路径分隔符：{name!r}")
    if name in (".", "..") or name.startswith(("\\", "/")):
        raise PathBoundaryError(f"非法名称：{name!r}")
    return name

# -*- coding: utf-8 -*-
"""
harness/tools/schema.py —— 参数 Schema 校验（步骤 25 的提前件）

零依赖的 JSON-Schema 子集校验：OpenAI function parameters 一般只用到
type + properties + required。校验失败返回可读错误文本，成功返回 None。
"""

from __future__ import annotations

_TYPES = {"string": str, "number": (int, float), "integer": int,
          "boolean": bool, "object": dict, "array": list}


def validate_arguments(spec: dict, args: dict) -> str | None:
    """spec：OpenAI parameters dict；args：模型给的参数。返回错误信息或 None。"""
    properties = spec.get("properties") or {}
    for req in spec.get("required") or []:
        if req not in args or args[req] is None:
            return f"缺少必填参数：{req}"
    for key, value in args.items():
        p = properties.get(key)
        if not p:
            continue  # 多余参数宽容放行（模型偶尔多给，不值得打断流程）
        expected = _TYPES.get(p.get("type"))
        if expected is None:
            continue
        ok = isinstance(value, expected)
        if p.get("type") == "integer" and isinstance(value, bool):
            ok = False
        if not ok:
            return f"参数 {key} 类型错误：期望 {p.get('type')}，实际 {type(value).__name__}"
    return None

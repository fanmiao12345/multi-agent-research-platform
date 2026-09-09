# -*- coding: utf-8 -*-
"""内置工具：calculator（DEV_PLAN A0.6 步骤 10）。

安全计算器：绝不使用 eval()，用 ast 解析成语法树后只放行白名单运算。
"""

from __future__ import annotations

import ast
import math
import operator

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs, "round": round, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "sqrt": math.sqrt, "log": math.log, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不支持的常量: {node.value!r}")
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        return _UNARYOPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("只支持数学函数：sin/cos/tan/sqrt/log/exp/round/abs")
        args = [_eval_node(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"不允许的变量: {node.id}")
    raise ValueError(f"不支持的语法: {type(node).__name__}")


def calculator(expression: str) -> str:
    """计算数学表达式并返回文本结果（工具输出统一为 str）。"""
    expr = (expression or "").strip()
    for zh, en in (("×", "*"), ("÷", "/"), ("乘以", "*"), ("除以", "/")):
        expr = expr.replace(zh, en)
    if not expr:
        raise ValueError("表达式为空")
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval_node(tree.body)
    except Exception as e:  # noqa: BLE001 —— 错误信息交给模型处理
        raise ValueError(f"无法计算「{expression}」：{e}") from e
    if isinstance(result, float) and result == int(result):
        result = int(result)
    return f"{expression} = {result}"

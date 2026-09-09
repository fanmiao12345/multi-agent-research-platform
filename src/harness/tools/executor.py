# -*- coding: utf-8 -*-
"""
harness/tools/executor.py —— Tool Executor（DEV_PLAN D3 / 步骤 24）

统一执行链：
    Schema Validation → Permission Check → Risk/Approval Check
    → Retry（仅 transient）→ Timeout → Execute → Output 文本化
任何一步失败都返回带前缀的文本，绝不抛异常打断 Agent 循环（模型可据此调整）。
"""

from __future__ import annotations

import time
import threading
from concurrent.futures import Future
from contextvars import copy_context
from src.harness.model_gateway import BudgetStop

from src.harness.control.policy import allowed as _policy_allowed
from src.harness.control.policy import requires_approval as _requires_approval
from src.harness.tools.registry import ToolRegistry
from src.harness.tools.result_processor import process as _process_result
from src.harness.tools.schema import validate_arguments

# 结果/错误前缀约定（模型和评测都靠它识别）
TOOL_ERROR = "[tool-error]"
TOOL_PERMISSION_DENIED = "[tool-permission-denied]"
TOOL_APPROVAL_REQUIRED = "[tool-approval-required]"


class ToolTimeoutError(TimeoutError):
    """等待已超时，但后台调用可能仍在执行；禁止自动重试。"""


def classify_error(e: Exception) -> str:
    """把异常粗略分类（transient/permission/validation/permanent）供重试决策。"""
    import urllib.error
    if isinstance(e, PermissionError):
        return "permission"
    if isinstance(e, (ValueError, TypeError)):
        return "validation"
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "transient"
    return "permanent"


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._pending: dict[str, Future] = {}
        self._lock = threading.Lock()

    def execute(self, name: str, arguments: dict, *,
                permissions: set | frozenset | None = None,
                approver=None, approval_handler=None) -> str:
        """执行工具并返回文本。approver：None 且工具 requires_approval → 拒绝。"""
        spec = self.registry.get(name)
        if spec is None:
            return f"{TOOL_ERROR} 没有名为 {name} 的工具。可用：{', '.join(t.name for t in self.registry.list())}"

        # 1) Schema Validation
        if not isinstance(arguments, dict):
            return f"{TOOL_ERROR} 参数必须是 object（工具：{name}）"
        err = validate_arguments(spec.parameters, arguments)
        if err:
            return f"{TOOL_ERROR} 参数校验失败：{err}（工具：{name}）"

        # 2)+3) Permission / Risk / Approval（统一走 control.policy）
        if permissions is not None and name not in permissions:
            return f"{TOOL_PERMISSION_DENIED} 未授权调用 {name}"
        if _requires_approval(spec) and not approver and approval_handler is not None:
            if approval_handler(spec, dict(arguments)) is not True:
                return f"{TOOL_PERMISSION_DENIED} 用户未批准工具 {name}"
            approver = True
        ok, reason = _policy_allowed(spec, permissions, approver)
        if not ok:
            if _requires_approval(spec) and not approver:
                return f"{TOOL_APPROVAL_REQUIRED} {reason}"
            return f"{TOOL_PERMISSION_DENIED} {reason}（工具：{name}）"

        # 4) Retry（仅 transient 类错误）→ 5) Timeout → 6) Execute → 7) 结果处理
        last_err: Exception | None = None
        for attempt in range(spec.retry_policy + 1):
            try:
                if spec.timeout:
                    result = self._call_with_timeout(spec, arguments, spec.timeout)
                else:
                    result = spec.func(**arguments)
                return _process_result(name, result)
            except BudgetStop:
                raise
            except Exception as e:  # noqa: BLE001 —— 统一文本化
                last_err = e
                kind = classify_error(e)
                if (isinstance(e, ToolTimeoutError) or spec.side_effect
                        or kind != "transient" or attempt >= spec.retry_policy):
                    break
                time.sleep(0.1 * (attempt + 1))
        return (f"{TOOL_ERROR} 工具 {name} 执行失败"
                f"（第 {attempt + 1} 次尝试失败，错误分类：{classify_error(last_err)}）：{last_err}")

    def _call_with_timeout(self, spec, arguments: dict, timeout: float) -> object:
        # daemon 不阻塞进程退出；它不提供强制终止或副作用撤销能力。
        with self._lock:
            previous = self._pending.get(spec.name)
            if previous is not None and not previous.done():
                raise ToolTimeoutError(f"工具 {spec.name} 的上次调用仍在执行，禁止重复启动")
            fut = Future()
            self._pending[spec.name] = fut

        def worker():
            try:
                fut.set_result(spec.func(**arguments))
            except BaseException as e:
                fut.set_exception(e)

        threading.Thread(target=copy_context().run, args=(worker,), daemon=True).start()
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            raise ToolTimeoutError(
                f"工具 {spec.name} 超时：超过 {timeout}s 未返回；后台调用可能仍在执行，未自动重试") from None

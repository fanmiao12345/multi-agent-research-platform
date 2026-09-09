"""一次用户请求的显式边界；B3起携带本地资料（texts/files），B4起携带URL并显式声明是否允许联网。"""
from dataclasses import asdict, dataclass
import math

from src.harness.storage.sources import MAX_SOURCES


@dataclass(frozen=True)
class TaskRequest:
    task: str
    mode: str = "mock"
    profile: str | None = None
    max_iterations: int = 8
    max_calls: int = 12
    max_output_tokens: int = 8192
    max_seconds: float = 300
    max_cost: float | None = None
    system_extra: str = ""
    # 本地资料：粘贴文本（内容）与本地文件路径（只读原文，从不回写）。
    texts: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    # 用户显式给出的网页链接（B4：抓取正文并入来源；"仅依据资料"模式也允许指定链接）。
    urls: tuple[str, ...] = ()
    # 是否允许联网研究（搜索等）；当前搜索服务未配置时该开关无实际效果。
    allow_network: bool = False
    # 执行流程（B5）：agent=通用 Agent 循环（默认）；research=资料整理/研究写作链
    # （证据→素材→提纲→初稿→审校→有限修订，需要可用资料）。
    flow: str = "agent"
    # 改稿模式（S5-04 单次改稿）：携带"原稿文本"，research 链改为在 base_draft 上修订：
    # 素材/提纲仍基于资料生成，初稿以原稿为上一稿并按任务要求改写；旧稿不覆盖。
    base_draft: str = ""

    def __post_init__(self):
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("task必须为非空文本")
        if self.mode not in ("mock", "real"):
            raise ValueError("mode必须为mock或real")
        if self.profile is not None and not isinstance(self.profile, str):
            raise ValueError("profile必须为字符串")
        if not isinstance(self.system_extra, str):
            raise ValueError("system_extra必须为文本")
        for name in ("max_iterations", "max_calls", "max_output_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "max_iterations" else 0):
                raise ValueError(name + "必须为有效非负整数，迭代上限至少为1")
        for name in ("max_seconds", "max_cost"):
            value = getattr(self, name)
            if value is None and name == "max_cost":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(name + "必须为非负有限数")
        for name in ("texts", "files", "urls"):
            value = getattr(self, name)
            if isinstance(value, (list, tuple)):
                object.__setattr__(self, name, tuple(value))  # 兼容 JSON 列表输入
                value = tuple(value)
            if not isinstance(value, tuple):
                raise ValueError(name + "必须为元组或列表")
            for item in value:
                if isinstance(item, bool) or not isinstance(item, str):
                    raise ValueError(name + "每一项必须是文本")
                # 粘贴文本允许为空串/空白：导入器分类为 empty（与文件空正文一致）；
                # 文件路径与网页链接必须有实际内容。
                if name != "texts" and not item.strip():
                    raise ValueError(name + "每一项必须为非空文本")
            if len(value) > MAX_SOURCES:
                raise ValueError(f"{name}超过单任务{MAX_SOURCES}个来源上限")
        if len(self.texts) + len(self.files) + len(self.urls) > MAX_SOURCES:
            raise ValueError(f"资料总数超过单任务{MAX_SOURCES}个来源上限，请缩小范围")
        if not isinstance(self.allow_network, bool):
            raise ValueError("allow_network必须为布尔值")
        if self.flow not in ("agent", "research"):
            raise ValueError("flow必须为agent或research")
        if not isinstance(self.base_draft, str):
            raise ValueError("base_draft必须为文本")

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求必须为JSON对象")
        force = payload.get("force_mock", False)
        if not isinstance(force, bool) or (force and payload.get("mode") == "real"):
            raise ValueError("force_mock格式无效或与真实模式冲突")
        values = {k: payload[k] for k in cls.__dataclass_fields__ if k in payload}
        if force:
            values["mode"] = "mock"
        values.setdefault("task", "")
        return cls(**values)

    def snapshot(self) -> dict:
        """落盘请求快照：不含粘贴正文与 base_draft（内容在 sources/ 与任务产物），其余字段保留。"""
        snapshot = asdict(self)
        snapshot.pop("texts", None)
        snapshot.pop("base_draft", None)
        return snapshot

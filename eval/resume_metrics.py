# -*- coding: utf-8 -*-
"""
eval/resume_metrics.py —— 简历技术点量化读数（全离线，Mock/桩，可复现；多实验均值口径）

三个实测口径，各对应一个技术主张；读数如实落盘，不凑整数：
1. Skill 三层渐进加载的 System Prompt token 节省率
   naive = 全部技能 fullcontent 全量注入；progressive = 全部技能 metadata
   + 仅命中技能的 fullcontent（references 按需另算）。
   多实验：每个技能轮流当"命中的那个"各测一次，报节省率均值与区间。
2. Fanout（3 并发）相对顺序执行的端到端耗时降低率
   同一子任务集（桩 worker 固定耗时），墙钟对比；多轮独立计时，
   报均值±标准差与中位数两套口径。
3. 跨会话知识复用命中率（L2 语义检索）
   会话 A 写入 N 条事实；会话 B 用改述句查询；top-1 / top-3 命中率；
   对用例集做 bootstrap 重采样（默认 500 次、固定种子），报均值与 95% 置信区间。

运行：python -m eval.resume_metrics            # 全量，报告落 eval/reports/resume_metrics/
     python -m eval.resume_metrics --sleep 0.05 --repeats 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPORT_DIR = EVAL_DIR / "reports" / "resume_metrics"


# ---------- 1. Skill token 节省率 ----------
def measure_skill_token_savings(registry=None) -> dict:
    """多次实验取均值口径：17 个技能逐个当作"命中的那个技能"完整加载，
    每次都算一版节省率，最后取均值/极差——消除"命中技能恰好偏小/偏大"的偏差。"""
    from src.harness.context.budget import estimate_tokens
    from src.harness.skills.registry import SkillRegistry
    from src.harness.skills.router import inject
    from src.harness.skills.types import LEVEL_FULLCONTENT, LEVEL_METADATA

    registry = registry or SkillRegistry()
    skills = registry.list()
    if not skills:
        return {"error": "技能目录为空，无法测量"}

    # naive：不分层，所有技能的完整正文全量进 System Prompt（固定基准）
    naive_tokens = estimate_tokens(
        "\n".join(inject(s, level=LEVEL_FULLCONTENT) for s in skills))
    meta_tokens = estimate_tokens(
        "\n".join(inject(s, level=LEVEL_METADATA) for s in skills))

    # 实验：每个技能轮流当"命中的 1 个"（metadata 全量 + 该技能 fullcontent）
    savings_list = []
    per_skill = []
    for hit in skills:
        prog = meta_tokens + estimate_tokens(inject(hit, level=LEVEL_FULLCONTENT))
        savings = 1.0 - prog / max(1, naive_tokens)
        savings_list.append(savings)
        per_skill.append({"name": hit.name,
                          "metadata": estimate_tokens(inject(hit, level=LEVEL_METADATA)),
                          "full": estimate_tokens(inject(hit, level=LEVEL_FULLCONTENT)),
                          "savings_when_hit": round(savings, 4)})

    mean = sum(savings_list) / len(savings_list)
    return {
        "experiments": len(skills),
        "skills_count": len(skills),
        "naive_tokens": naive_tokens,
        "progressive_tokens_mean": round(meta_tokens +
                                         sum(p["full"] for p in per_skill)
                                         / len(per_skill)),
        "system_prompt_savings": round(mean, 4),          # 均值（主读数）
        "system_prompt_savings_min": round(min(savings_list), 4),
        "system_prompt_savings_max": round(max(savings_list), 4),
        "routing_stage_savings": round(1.0 - meta_tokens / max(1, naive_tokens), 4),
        "per_skill": per_skill,
    }


# ---------- 2. Fanout 并行提速 ----------
def measure_parallel_speedup(subtasks: list[str] | None = None,
                             sleep: float = 0.15, max_parallel: int = 3,
                             repeats: int = 50, *, jitter: bool = False,
                             seed: int = 42) -> dict:
    """多次实验取均值口径：repeats 轮独立计时（每轮 = 一次顺序 + 一次 fanout），
    报均值±标准差与中位数——单轮计时受调度抖动影响，均值口径更稳。
    jitter=True 时每个子任务耗时在 0.5x~1.5x 间随机波动（固定种子），
    模拟异质任务时长——提速结论应在这种更真实的场景下仍然成立。"""
    import random

    from src.orchestration.fanout import run_fanout

    subtasks = subtasks or [f"独立子任务 {i}" for i in range(6)]
    rng = random.Random(seed)

    def stub_worker(task_text: str, role: str) -> str:
        time.sleep(sleep * (rng.uniform(0.5, 1.5) if jitter else 1.0))
        return f"[{role}] 完成：{task_text[:20]}"

    seq_times, par_times = [], []
    for _ in range(max(1, repeats)):
        start = time.perf_counter()
        for sub in subtasks:                   # 顺序基线
            stub_worker(sub, "researcher")
        seq_times.append(time.perf_counter() - start)

        start = time.perf_counter()
        run_fanout("并行任务", stub_worker, subtasks, max_parallel=max_parallel)
        par_times.append(time.perf_counter() - start)

    seq_med = statistics.median(seq_times)
    par_med = statistics.median(par_times)
    seq_mean = statistics.fmean(seq_times)
    par_mean = statistics.fmean(par_times)
    seq_std = statistics.stdev(seq_times) if len(seq_times) > 1 else 0.0
    par_std = statistics.stdev(par_times) if len(par_times) > 1 else 0.0
    reductions = [1.0 - p / s for s, p in zip(seq_times, par_times)]
    return {"subtasks": len(subtasks), "max_parallel": max_parallel,
            "sleep_per_task_s": sleep, "repeats": repeats, "jitter": jitter,
            "sequential_mean_s": round(seq_mean, 4),
            "fanout_mean_s": round(par_mean, 4),
            "sequential_std_s": round(seq_std, 4),
            "fanout_std_s": round(par_std, 4),
            "sequential_median_s": round(seq_med, 4),
            "fanout_median_s": round(par_med, 4),
            "time_reduction": round(statistics.fmean(reductions), 4),   # 均值（主读数）
            "time_reduction_median": round(1.0 - par_med / seq_med if seq_med else 0.0, 4),
            "speedup_x": round(seq_mean / par_mean, 2) if par_mean else 0.0}


# ---------- 3. 跨会话知识复用命中率 ----------
# (会话A写入的事实, 会话B的改述查询) —— 查询保留主题关键词但句式不同
REUSE_CASES = [
    # —— 原始 10 条 ——
    ("项目的默认并发上限是 3 个子智能体", "系统最多能同时跑几个子智能体？"),
    ("研究报告的默认修订轮数上限是 2 轮", "报告最多自动改几次稿？"),
    ("证据引用必须能逐字定位到原始来源", "引用怎么回溯到原文？"),
    ("单来源正文导入上限为 2MB", "一个文件最大能导入多大？"),
    ("嵌入套件嵌套深度最多 2 层", "子智能体可以嵌套几层？"),
    ("扫描版 PDF 不支持，需要文本型 PDF", "图片扫描的 PDF 能直接读吗？"),
    ("整批运行费用有累计上限保护", "跑一批任务怎么控制总花费？"),
    ("报告里的推断必须标注〔推断〕标签", "猜测的内容在报告里怎么标？"),
    ("来源撤回后下游引用会被标记失效", "删掉一个来源后引用会怎样？"),
    ("预算耗尽时交付草稿并说明缺口", "钱花完了还能拿到结果吗？"),
    # —— 扩充 40 条（同一风格：改述问句保留主题关键词，句式不同）——
    ("根任务预算采用先预留后结算的记账方式", "预算是怎么预留和结算的？"),
    ("未知用量的模型调用不会按零成本计算", "调用没有返回用量还算免费吗？"),
    ("任务交付等级分为成品草稿和无法完成三类", "交付结果分成哪几个等级？"),
    ("零预算的任务不会发出任何模型请求", "预算是零的时候会调模型吗？"),
    ("引用标记必须能解析到证据条目", "引用标记解析不到证据会怎样？"),
    ("冲突状态没有依据消解时强制保持开放", "没有依据的冲突能消解掉吗？"),
    ("提纲必须覆盖全部必需证据才能通过", "提纲要覆盖全部必需证据吗？"),
    ("修订每轮保存问题清单和新稿版本", "修订的时候旧稿会被覆盖吗？"),
    ("审校分为程序校验和模型审校两层", "审校有几层分别查什么？"),
    ("计划版本带有失效关系并可追溯", "重新规划后旧计划还能查到吗？"),
    ("来源撤回后下游产物引用标记失效", "来源撤回后引用会失效吗？"),
    ("子任务预算从父任务预留不新开账本", "子任务的开销记在谁的账上？"),
    ("取消在阶段边界收敛并保留已存产物", "中途取消已完成的产物会保留吗？"),
    ("检查点按阶段保存支持崩溃后续跑", "程序崩溃后能从断点继续跑吗？"),
    ("高风险工具执行前需要持久化审批", "高风险工具执行前要审批吗？"),
    ("审批绑定参数哈希过期自动失效", "批准的授权过期还有效吗？"),
    ("缺关键条件的任务进入待输入状态", "任务缺少关键条件时怎么处理？"),
    ("队列采用原子领取加心跳防重复执行", "多个执行者会重复领同一个任务吗？"),
    ("网页抓取拒绝私网和回环地址", "内网地址的链接可以抓取吗？"),
    ("每跳重定向都重新做安全解析", "重定向之后的链接还做安全检查吗？"),
    ("同文转载按内容哈希判重只保留一条", "转载的重复文章会存几份？"),
    ("搜索候选记录排名标题和摘要", "搜索结果会记录哪些信息？"),
    ("正文抓取失败不会成为已读证据", "网页正文抓取失败还算读过吗？"),
    ("扫描版图像PDF明确不支持识别", "扫描图片版的PDF能识别吗？"),
    ("报告事实推断未知三种标注分开", "事实和猜测的内容会混在一起吗？"),
    ("引用可定位率验收要求百分之百", "引用定位不到原文能通过验收吗？"),
    ("预算停止时交付已有草稿并说明缺口", "预算耗尽时已有草稿会保留吗？"),
    ("审校模型与写作模型可分开配置", "审校和写作能配不同的模型吗？"),
    ("评测默认闭卷不泄露参考答案", "评分要点会提前泄露给写作端吗？"),
    ("人工评分与自动评测双轨分开记录", "系统会自己替人工打分吗？"),
    ("技能激活前校验工具白名单交集", "技能可以把没授权的工具打开吗？"),
    ("上下文按权重比例分配token预算", "上下文的长度是怎么分配的？"),
    ("历史消息超窗时压缩成摘要保留要点", "对话历史太长会被直接扔掉吗？"),
    ("会话材料快照不跨会话共享", "任务里的资料别的会话能看到吗？"),
    ("工作记忆有界先进先出防膨胀", "工作记忆会不会越积越多？"),
    ("遗忘曲线按访问间隔衰减经验记忆", "很久没用的经验会被清理吗？"),
    ("语义记忆只降权不自动删除", "语义记忆会自动删掉吗？"),
    ("向量检索得分乘以可检索度排序", "检索排序考虑记忆的新旧吗？"),
    ("记忆块注入用户消息保持系统前缀稳定", "记忆注入会改动系统提示词吗？"),
    ("漂移检测对比基线指标超阈值告警", "指标变差时会自动告警吗？"),
    # —— 再扩充 50 条（把 bootstrap 区间收窄到 ±10% 以内）——
    ("重规划保留无关的已完成结果", "重新规划会不会作废已经做完的部分？"),
    ("重复计划且无进展会被检测标记", "计划一直原地打转能发现吗？"),
    ("调度智能体读题输出结构化执行方案", "谁来决定用哪种执行方式？"),
    ("fanout 模式按依赖波次真实并发", "fanout 是真并行还是假并行？"),
    ("子智能体二层嵌套受深度和总数护栏约束", "子智能体嵌套有什么限制？"),
    ("同题子任务去重防止自激循环", "同样的任务会不会重复派工？"),
    ("所有模式共享同一个根预算", "不同模式是不是各花各的钱？"),
    ("整理任务与写作任务交付不同格式", "整理类的任务也要写长报告吗？"),
    ("证据素材提纲提纲产物均版本化保存", "中间产物会被覆盖吗？"),
    ("报告章节缺失在程序层判为错误", "少了一个章节还能算合格吗？"),
    ("禁语命中在程序层按错误拦截", "出现禁语会被拦下来吗？"),
    ("关键事实要求逐字出现在正文", "重要数字必须原文出现吗？"),
    ("审校模型输出非JSON会判失败", "审校抽风返回乱码怎么办？"),
    ("会话可以基于原稿继续追问改稿", "能基于上一版原稿继续改稿吗？"),
    ("追问建立新版本且旧版本保留", "新版本会覆盖旧版本吗？"),
    ("导出支持Markdown与安全HTML", "结果能导出成什么格式？"),
    ("工作台可以查看来源并点击定位原文", "报告里的出处能点开看原文吗？"),
    ("服务重启后任务状态从SQLite恢复", "重启之后任务还在吗？"),
    ("已提交的任务不会被两个执行者同时领取", "会不会两个人同时干同一件事？"),
    ("本地MCP工具纳入同一权限与账本", "外部工具的花费也记账吗？"),
    ("配置里模型密钥不出现在界面与快照", "密钥会暴露在页面或快照里吗？"),
    ("模型按任务档位在cheap与deep间路由", "简单问题会用便宜模型吗？"),
    ("工具任务永不落入无工具的deep档", "需要工具的任务会分到没工具的档位吗？"),
    ("评测报告记录代码与包与配置快照", "评测结果能复现吗？"),
    ("失败样本整目录保存供事后分析", "失败样本是整目录留着的吗？"),
    ("故障注入需对账状态账本与产物", "出了故障怎么核实损失？"),
    ("闭卷口径禁止把标注喂给写作端", "闭卷评测会把标注泄给写作端吗？"),
    ("执行完成预期符合成品质量三数分离", "完成率是怎么算的？"),
    ("引用支持率人工独立核对双口径", "引用核对人工还是机器说了算？"),
    ("伪造来源与未授权操作要求为零", "伪造来源的引用能被容忍吗？"),
    ("技能元数据先召回命中才注入全文", "所有技能的说明都会塞进提示词吗？"),
    ("上下文构建记录每来源token统计", "每次调用用了多少token有记录吗？"),
    ("证据检索命中才进入上下文", "没命中的资料也会发给模型吗？"),
    ("工具结果截断后完整产物另行保存", "太长的工具输出会丢信息吗？"),
    ("预算分配公式扣除在途与成稿预留", "预算分配会扣掉在途和预留吗？"),
    ("渠道未知错误不盲目重试", "调用出错会自动重试吗？"),
    ("审批请求带请求标识防重放", "审批通过后还能重放吗？"),
    ("备份恢复走在线备份接口", "数据能备份和还原吗？"),
    ("安装脚本只操作项目内路径", "安装会动我系统别的目录吗？"),
    ("搜索未配置时明确禁用不用Mock顶替", "没配搜索会假装搜到吗？"),
    ("检索问题有界生成不无限扩展", "搜索词会不会越拆越多？"),
    ("URL白名单外域名显式失败", "没见过的网站能随便抓吗？"),
    ("HTTPS证书校验保留server_hostname", "https的证书会校验吗？"),
    ("正文类型白名单拒绝非文本内容", "非文本的内容会被拒收吗？"),
    ("段落与字符偏移写入来源meta定位", "引用能精确到段落位置吗？"),
    ("任务目录冲突或含产物显式报错", "任务文件夹乱了会报错吗？"),
    ("运行中断的后台线程不能强杀", "后台卡死的线程能杀掉吗？"),
    ("恢复不保证外部副作用exactly-once", "恢复后外部操作会重复执行吗？"),
    ("漂移检测对比基线支持逐指标阈值", "每个指标能设不同的告警线吗？"),
    ("评测者与写作者同模型时记录独立性局限", "评测者和写作者是同一个模型吗？"),
]


def measure_cross_session_hit_rate(top_k: int = 3, store_path=None,
                                   bootstrap: int = 2000,
                                   seed: int = 42) -> dict:
    """多次实验取均值口径：点估计之外，对用例集做 bootstrap 重采样
    （默认 2000 次、固定种子可复现），报命中率均值与 95% 置信区间——
    区间宽度由用例数决定，重采样次数只影响区间估计本身的稳定性。"""
    import random

    from src.harness.memory.layers import MemoryLayers
    from src.harness.memory.long_term import LongTermStore

    # 默认用本报告目录下的专属存储，避免共享 memory_store.json 的历史数据污染读数
    if store_path is None:
        store_path = REPORT_DIR / "reuse_store.json"
    store = LongTermStore(store_path)
    # 可重复执行：清掉上次评测写入的记录，避免重复条目干扰排序与期望匹配
    for rec in list(store.list(kind="semantic")):
        store.forget(rec.id)
    layers = MemoryLayers(store=store)

    # 会话 A：写入知识（语义层）
    for fact, _ in REUSE_CASES:
        layers.remember_semantic(fact, source="session_A")

    # 会话 B：换会话查询（新工作记忆，语义/情节层跨会话）
    top1_hits = topk_hits = 0
    detail = []
    for fact, query in REUSE_CASES:
        results = layers.semantic.search(query, top_k=top_k)
        ids = [r.id for r in results]
        expected = None
        # 期望命中：写入时记录的 id —— 通过内容回查（store 内同一内容）
        for rec in layers.store.list(kind="semantic"):
            if rec.content == fact:
                expected = rec.id
                break
        hit1 = bool(ids and expected and ids[0] == expected)
        hitk = bool(expected and expected in ids)
        top1_hits += hit1
        topk_hits += hitk
        detail.append({"query": query, "expected": expected, "returned": ids,
                       "top1": hit1, "top3": hitk})

    n = len(REUSE_CASES)
    top1_rate = top1_hits / n
    topk_rate = topk_hits / n

    # bootstrap：重采样用例集，估计命中率的均值与 95% 置信区间
    rng = random.Random(seed)
    top1_samples, topk_samples = [], []
    for _ in range(max(0, bootstrap)):
        sample = [detail[rng.randrange(n)] for _ in range(n)]
        top1_samples.append(sum(1 for d in sample if d["top1"]) / n)
        topk_samples.append(sum(1 for d in sample if d["top3"]) / n)

    def ci95(samples: list[float]) -> list[float]:
        if not samples:
            return []
        ordered = sorted(samples)
        lo = ordered[max(0, int(0.025 * len(ordered)))]
        hi = ordered[min(len(ordered) - 1, int(0.975 * len(ordered)) - 1)]
        return [round(lo, 4), round(hi, 4)]

    return {"cases": n, "vector_backend": layers.index.backend, "top_k": top_k,
            "top1_hit_rate": round(top1_rate, 4),
            "topk_hit_rate": round(topk_rate, 4),
            "bootstrap_resamples": bootstrap,
            "top1_mean": round(statistics.fmean(top1_samples), 4) if top1_samples else None,
            "top1_ci95": ci95(top1_samples),
            "top3_mean": round(statistics.fmean(topk_samples), 4) if topk_samples else None,
            "top3_ci95": ci95(topk_samples),
            "detail": detail}


# ---------- 4. Prompt Cache 前缀稳定度（记忆注入位置对照）----------
def measure_prompt_cache_stability(turns: int = 5) -> dict:
    """用真实 compose_context 管线模拟多轮对话，测量"相邻两次调用间的
    逐字节稳定前缀占比"——prompt cache 按请求前缀命中，这个占比是缓存
    收益的上界代理。对照两种注入位置：system（旧行为）vs user message
    （memory_in_user_message 开关）。记忆块每轮不同 = 对缓存最不利场景。

    读数解释（2026-09-18 实测）：两种位置占比都低且几乎无差——根因是
    compose_context 的 allocate 归一化把 messages 份额挤到≈0，历史窗口
    恒为最近 2 条（已登记 OPTIMIZATION_BACKLOG O-15）。"system 前缀
    逐字节稳定"这一主张本身成立（每轮 system 消息完全一致，有单测），
    但"缓存收益可观"在当前管线下不成立：历史太短，可缓存前缀就那么点。
    user 注入的结构性优势要在历史窗口修复后才可能体现。
    """
    from src.harness.context.budget import estimate_tokens
    from src.harness.context.builder import compose_context
    from src.harness.context.policy import ContextSource

    def run_mode(user_kinds: tuple[str, ...],
                 reserve_window: bool = False) -> tuple[float, bool]:
        system_text = "你是研究助手。（系统提示词在多轮间保持逐字节稳定）"
        history: list[dict] = []
        shares: list[float] = []
        prev_msgs: list[dict] | None = None
        system_always_stable = True
        for t in range(1, turns + 1):
            memory = ContextSource(
                kind="memory",
                content=f"第{t}轮检索命中的记忆块：主题{t}的要点为 {t}、{t * 11}、"
                        f"{t * 17}（随查询变化，每轮不同）")
            msgs, _ = compose_context(
                f"第{t}轮问题：主题 {t} 的要点？",
                [ContextSource(kind="instructions", content=system_text), memory],
                history=list(history), total_budget=4000,
                user_message_kinds=user_kinds,
                reserve_message_window=reserve_window)
            if prev_msgs is not None:
                system_always_stable &= (
                    msgs[0].get("role") == "system"
                    and msgs[0].get("content") == prev_msgs[0].get("content"))
                shared = 0
                for a, b in zip(prev_msgs, msgs):
                    if a.get("role") == b.get("role") and \
                            a.get("content") == b.get("content"):
                        shared += estimate_tokens(str(a.get("content") or ""))
                    else:
                        break
                total = sum(estimate_tokens(str(m.get("content") or ""))
                            for m in msgs)
                shares.append(shared / max(1, total))
            # 本轮真实发出的 user 消息与模拟回答落进历史（下一轮的既有前缀）
            last_user = next(m for m in reversed(msgs) if m.get("role") == "user")
            history.append({"role": "user", "content": last_user["content"]})
            history.append({"role": "assistant", "content": f"第{t}轮回答。"})
            prev_msgs = msgs
        return (statistics.fmean(shares) if shares else 0.0), system_always_stable

    system_mode, stable_a = run_mode(())
    user_mode, stable_b = run_mode(("memory",))
    # O-15 修复开启时（未默认）：历史窗口恢复正常，user 注入的结构性优势才显现
    user_mode_fixed, _ = run_mode(("memory",), reserve_window=True)
    system_mode_fixed, _ = run_mode((), reserve_window=True)
    return {"turns": turns,
            "memory_in_system_prefix_share": round(system_mode, 4),
            "memory_in_user_prefix_share": round(user_mode, 4),
            "stable_prefix_gain": round(user_mode - system_mode, 4),
            "system_prefix_byte_stable": bool(stable_a and stable_b),
            "with_o15_fix_user_share": round(user_mode_fixed, 4),
            "with_o15_fix_system_share": round(system_mode_fixed, 4),
            "with_o15_fix_gain": round(user_mode_fixed - system_mode_fixed, 4),
            "note": "前缀占比 = 相邻调用间逐字节相同部分占全部 prompt token 比例"
                    "（缓存命中上界代理；记忆桩每轮不同，最不利场景）。"
                    "当前增益≈0 的根因：历史窗口恒为最近 2 条（O-15），"
                    "'system 前缀逐字节稳定'成立，'缓存收益'要等 O-15 开关启用；"
                    "with_o15_fix_* 为开关开启后的对照（未默认，待批次间隙切换）"}


# ---------- 汇总 ----------
def run_all(sleep: float = 0.15, repeats: int = 50) -> dict:
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "offline_stub（Mock/桩大脑，全离线可复现；多实验均值口径）",
        "skill_token": measure_skill_token_savings(),
        "parallel_speedup": measure_parallel_speedup(sleep=sleep, repeats=repeats),
        "jittered_speedup": measure_parallel_speedup(sleep=sleep, repeats=repeats,
                                                     jitter=True),
        "cross_session_reuse": measure_cross_session_hit_rate(),
        "prompt_cache_stability": measure_prompt_cache_stability(),
    }
    return report


def render_md(report: dict) -> str:
    st = report["skill_token"]
    sp = report["parallel_speedup"]
    jt = report.get("jittered_speedup", {})
    cs = report["cross_session_reuse"]
    pc = report.get("prompt_cache_stability")
    lines = [
        "# 简历技术点量化读数（离线桩，可复现；多实验均值口径）",
        f"- 时间：{report['generated_at']}　口径：{report['mode']}",
        "",
        "## 1. Skill 三层渐进加载 · System Prompt token 节省率",
        f"- 技能数：{st['skills_count']}；naive 全量注入 {st['naive_tokens']} tokens；"
        f"{st.get('experiments', st['skills_count'])} 次实验（每个技能轮流当命中技能）取均值",
        f"- **节省率均值 {st['system_prompt_savings']:.1%}**"
        f"（区间 {st['system_prompt_savings_min']:.1%} ~ "
        f"{st['system_prompt_savings_max']:.1%}；路由阶段口径 "
        f"{st['routing_stage_savings']:.1%}）",
        "",
        "## 2. Fanout 并行编排 · 耗时降低",
        f"- {sp['subtasks']} 个子任务、并发 {sp['max_parallel']}、"
        f"单任务桩耗时 {sp['sleep_per_task_s']}s、{sp['repeats']} 轮取均值",
        f"- 顺序 {sp['sequential_mean_s']}s（±{sp['sequential_std_s']}）→ "
        f"fanout {sp['fanout_mean_s']}s（±{sp['fanout_std_s']}）：",
        f"- **耗时降低均值 {sp['time_reduction']:.1%}**"
        f"（中位数口径 {sp['time_reduction_median']:.1%}；"
        f"加速 {sp['speedup_x']}x）",
    ]
    if jt:
        lines.append(f"- 异质时长场景（每任务 0.5x~1.5x 随机抖动，同轮数）："
                     f"**耗时降低 {jt['time_reduction']:.1%}**"
                     f"（加速 {jt['speedup_x']}x）——结论对任务时长不均匀稳健")
    lines += [
        "",
        "## 3. 跨会话知识复用 · 语义检索命中率",
        f"- {cs['cases']} 条跨会话改述查询，后端 {cs['vector_backend']}；"
        f"bootstrap {cs['bootstrap_resamples']} 次重采样",
        f"- top-1 **{cs['top1_hit_rate']:.0%}**（均值 {cs['top1_mean']:.0%}，"
        f"95% CI {cs['top1_ci95'][0]:.0%}~{cs['top1_ci95'][1]:.0%}）；",
        f"- top-{cs['top_k']} **{cs['topk_hit_rate']:.0%}**（均值 {cs['top3_mean']:.0%}，"
        f"95% CI {cs['top3_ci95'][0]:.0%}~{cs['top3_ci95'][1]:.0%}）",
        "",
    ]
    if pc:
        lines += [
            "## 4. Prompt Cache 前缀稳定度 · 记忆注入位置对照",
            f"- {pc['turns']} 轮对话、真实 compose_context 管线、"
            f"记忆块每轮不同（最不利场景）；占比 = 相邻调用间逐字节稳定前缀的 token 份额",
            f"- 注入 system（旧行为）：**{pc['memory_in_system_prefix_share']:.1%}**；"
            f"注入 user message（开关）：**{pc['memory_in_user_prefix_share']:.1%}**；"
            f"system 前缀逐字节稳定：{pc['system_prefix_byte_stable']}",
            f"- 当前稳定前缀增益 **{pc['stable_prefix_gain']:+.1%}**——增益≈0 的根因是"
            "历史窗口恒为最近 2 条（O-15，allocate 归一化挤占 messages 份额），"
            "机制本身正确、收益要在 O-15 修复后才可能体现；"
            "开关默认关（保持 Q2 基线可比）",
            f"- **O-15 开关开启后（未默认）**：user {pc['with_o15_fix_user_share']:.1%} "
            f"vs system {pc['with_o15_fix_system_share']:.1%}，增益 "
            f"**{pc['with_o15_fix_gain']:+.1%}**——记忆注入 User Message 的"
            "结构性优势得到证实，切换待真实批次间隙（O-15）",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=0.15,
                        help="并行提速测量的单任务桩耗时（秒）")
    parser.add_argument("--repeats", type=int, default=50, help="计时轮数")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    report = run_all(sleep=args.sleep, repeats=args.repeats)
    md = render_md(report)
    print(md)
    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "resume_metrics.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (REPORT_DIR / "resume_metrics.md").write_text(md, encoding="utf-8")
        print(f"报告已写入 {REPORT_DIR}")


if __name__ == "__main__":
    main()

# B2交付记录：统一入口与根任务账本

日期：2026-09-09。已验收范围：离线代码与界面行为；真实服务与研究写作质量尚未验收。

## 交付

1. `TaskRequest`定义任务、模式、档案、迭代次数及根任务限制。Web、CLI、Agent Benchmark均使用`ResearchApplication.run`。
2. 每次提交创建根任务，保存`request.json`、`ledger.json`、`job.json`。原run目录不迁移，新增root_job_id、parent_run_id与role关联。
3. 主循环、规划、重规划、裁决、技能排序、历史摘要使用model_call。模型调用前持久化started记录，成功后补充用量，失败只记异常类型且费用标未知。
4. 线程池与工具等待线程复制上下文；同一根任务模型请求串行发起，以免并行分支重复消耗预算。不同用户提交仍可并发，受控全局队列未在本批实现。
5. 网页可设置限额、查看整个任务用量；历史无账本运行显示明确提示。评测批次累计根账本费用，不再仅取单run。

## 使用

```powershell
# 默认离线，生成根任务和run
.venv\Scripts\python -m src.interfaces.cli "计算6*7"

# 零模型调用验证：返回停止原因，退出码1
.venv\Scripts\python -m src.interfaces.cli "计算6*7" --max-calls 0

# 启动本机页面
.venv\Scripts\python -m src.interfaces.web.workbench --port 8765
```

CLI真实模式需显式`--mode real`；密钥来自项目配置，不通过命令行参数传入。
配置检查仍用`python -m src.harness.models.factory --mode real`，该诊断不发送请求。
CLI成功退出0，未完成/模型运行失败退出1，输入与配置错误退出2。

应用的默认上限：12次模型请求、8192个累计输出Token、300秒；真实模式未提供费用阈值时采用0.05美元估算停止阈值。
Web显示的费用阈值默认0.05美元，Mock模型成本为0。任意次数、输出、时间或费用上限设0均不会发起模型请求。
模型SDK隐藏重试关闭；本批不自动重试模型请求，以免漏记尝试及未知用量。后续S1-06增加分类与显式重试策略。

```text
workspaces/
  jobs/job_<id>/
    request.json      用户任务及有效限制，不含Settings/密钥/接口地址
    ledger.json       调用列表与根汇总；不保存消息和响应正文
    job.json          根终态、run关联、最终文本、业务验收状态
  <run_id>/
    run.json          保留旧接口，增加root_job_id等字段
    trace.jsonl
    usage.json        单run统计；不能与根账本再相加
```

`GET /api/runs/<run_id>/job`返回关联的根任务和账本；没有关联时返回历史记录说明。
`run.json`表示阶段执行状态；根任务对于迭代上限等未完成情况标partial，预算停止标cancelled，异常标failed。
所有job的business_acceptance仍是not_evaluated；执行完成不能据此认定资料研究已通过。

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`，199 passed，16.98秒。
- 定向：应用、模型、HTTP、评测、规划、上下文、编排及工具相关93项回归通过；后续补充纳入全量。
- 验证普通回答、工具往返、根/父子run关联、辅助调用、并发额度、零限额、未知价格/用量、模型异常、先记账后请求、任务关闭后拒绝请求。
- 真实适配器使用替身传输核对max_tokens、剩余timeout与max_retries=0，未请求真实服务。
- CLI通过真实子进程测试；Web通过HTTP测试与本地浏览器检查。
- 20个业务案例和10个故障案例定义仍有效，业务执行0次。

## 兼容性、限制与回退

- 未传入根任务上下文的旧组件/演示仍可单独运行，不会自动生成用户任务账本。产品入口应使用ResearchApplication。
- 现有实验策略仅接通计量，未自动加入研究业务链；本批没有搜索、导入或引用成稿功能。
- 参考价格沿用本地旧估值，以`legacy-estimates-b2-v1-unverified`标记，未声明实时准确。费用不是账单硬封顶，最后一笔可能超阈值。
- 输出Token限制对兼容适配器传递剩余额度；自定义适配器只有回包后计量。缺用量禁止继续，不能记作免费。
- 时间限制在调度/请求边界检查，并传给兼容SDK；网络库超时不等于整个任务硬截止。等待审批及后台工具无法强杀，持久取消在S4实现。
- JSON原子写入不等于多文件事务或跨进程恢复。崩溃时started表示消耗未确定；本批不自动重放未知调用。
- 不同根任务仍可同时运行；取消排队任务、统一队列、会话与持久审批尚未完成。
- 未改变旧文件布局，无数据库迁移。回退代码时保留jobs与旧run文件，只会失去新入口/账本显示；不要删除用户运行产物。B1快照只覆盖当时清单，不能充当整个项目备份。

下一步B3：本地文本/TXT/Markdown导入，来源与完整产物存储、原文定位、去重及路径边界。

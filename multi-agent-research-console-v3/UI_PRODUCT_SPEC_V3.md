# Research Console V3：基于项目文档的前端产品设计说明

这版不是“重新换颜色”，而是根据仓库当前产品文档重新做信息架构。

## 一、设计依据

重点参考：

- `docs/PROJECT_MASTER_PLAN.md`
- `docs/PRACTICAL_RESEARCH_WRITING_PLAN.md`
- `docs/S5_DELIVERY.md`
- `docs/FEATURE_FREEZE.md`
- `docs/EXECUTION_STATUS.md`
- `docs/DYNAMIC_ORCHESTRATION_PLAN.md`
- `docs/SKILL_CATALOG.md`
- `docs/architecture.md`
- `DEV_PLAN_LangGraph_Harness_From_Scratch.md`

文档里最重要的产品原则：

1. 用户一次输入目标，系统自动选择执行方式并推进。
2. 用户不需要充当项目经理，不需要逐个决定工具和Agent。
3. 首页围绕“目标输入与结果”，计划/子任务/调用细节默认收起。
4. 交付时告诉用户：结果、依据、执行方式及理由、用量/费用、等级与局限。
5. 来源和证据必须能从结论点回到原文。
6. waiting_input / waiting_human / budget / cancel / resume 等状态要给可操作说明。
7. 技术Trace是可观测能力，不应该成为普通用户主路径。

## 二、最终页面结构

### 1. 首页 `#/home`

用户只做一件核心事情：描述希望得到的结果。

页面包含：

- 大输入区
- 资料入口
- 联网开关
- “先看计划”开关
- 高级设置
- 最近任务
- 三个常见任务示例

默认不让用户选择多Agent模式。高级设置里保留显式覆盖。

### 2. 任务中心 `#/tasks`

展示：

- 任务目标
- 状态
- 当前阶段
- 更新时间

筛选：

- 全部
- 进行中
- 已交付
- 需要处理

点击任何任务进入独立详情，不在列表下方展开一堆面板。

### 3. 任务详情 `#/job/<job_id>`

默认Tab是“结果”。

顶部固定展示：

- 状态
- 实际执行方式
- 交付等级
- 来源数量
- 模型调用/费用
- 停止/恢复/导出

四个Tab：

- 结果
- 资料与产物
- 执行过程
- 版本与改稿

#### 结果

主区是长报告阅读器，右侧是证据核验。

点击报告中的 `[E-001]`：
→ 右侧立即显示事实、标注、原文、来源、定位。

这才是“证据可追溯”的用户路径。

#### 资料与产物

左侧来源/产物列表，右侧全文查看器。

点击来源：
→ 查看原文。

点击产物：
→ 查看产物内容。

#### 执行过程

普通用户不需要看Trace，但可以看：

- 选择了哪种编排方式
- 为什么选择
- 子任务/角色
- 阶段结果

原始 `orchestration.json` 折叠在底部。

#### 版本与改稿

展示历史Artifact版本，并支持追问改稿生成新版本。

### 4. 运行观测 `#/observe`

专门给开发/排障使用：

- Run列表
- Final Answer
- Trace
- Tool Cards
- Plan
- Streaming Events
- HITL

不与业务任务详情混在一起。

### 5. 配置与评测 `#/settings`

展示：

- 模型配置是否可用
- Provider / Model
- API Key是否已配置（不显示内容）
- 搜索Provider
- 本地任务统计
- benchmark摘要

## 三、浏览器适配

### ≥ 1180px

- 左侧导航：228px
- 主区完全流式
- 首页最大宽：1180px
- 任务中心：1360px
- 任务详情：1540px
- 报告 + 证据：主区自适应 + 330px侧栏

### 900–1179px

- 左侧导航折叠成76px
- 任务详情仍优先双栏
- 报告证据侧栏缩为285px

### ≤ 900px

- 报告/证据改单列
- 来源列表与查看器改单列
- 运行观测改单列

### ≤ 720px

- 取消桌面侧栏
- 底部移动导航
- 内容左右12px
- 报告阅读区单列
- 操作按钮自动换行

## 四、为什么不继续沿用之前的“大Dashboard”

旧方案最大的问题不是颜色，而是信息架构：

- 新建任务、任务队列、来源、Run、Trace、Tools、HITL、Eval全部平铺。
- 用户打开首页就看到系统内部结构。
- 任务结果没有真正独立的阅读空间。
- 点击关系弱，所有功能都在同一页上下滚动。
- 宽屏下内容仍像多个卡片拼接，没有浏览器应用的层级感。

V3改成“首页→任务→详情”的应用结构。

## 五、兼容范围

V3不改Python后端，仅替换`INDEX_HTML`。

继续使用现有接口：

- `/api/runs`
- `/api/config`
- `/api/eval`
- `/api/jobs`
- `/api/jobs/<id>/progress`
- `/api/jobs/<id>/sources`
- `/api/jobs/<id>/evidence`
- `/api/jobs/<id>/artifacts`
- `/api/jobs/<id>/process`
- `/api/jobs/<id>/cancel`
- `/api/jobs/<id>/resume`
- `/api/jobs/<id>/revise`
- `/api/jobs/<id>/input`
- `/api/runs/<id>/trace`
- `/api/runs/<id>/tools`
- `/api/runs/<id>/plan`
- `/api/runs/<id>/hitl`

同时保留已有测试依赖的DOM ID和关键JS函数名称。

## 六、入库后的合并差异（2026-09-22）

本目录的 `research_console_v3.html` 已同步为**仓库当前实际页面**：再次运行 `apply_research_console_v3.py` 为幂等，
不会丢下面的合并项。相对最初交付的 V3 稿，入库时补齐了：

1. 任务时间按 Unix 秒解析（原稿按毫秒 → 全部任务显示 1970），统一显示为 `YYYY-MM-DD HH:MM`。
2. "先看计划"读 `plan.subtasks` / `plan.reason`（原稿只读 `plan.tasks` / `meta.reason`，启发式方案会退化成 JSON），
   并显示预算与预计用量、方式名走中文映射。
3. `reopenCurrentJob()`：补充输入、恢复、顶部刷新后重新进入同一任务；原稿只做 `openJob` 会因早退而停更。
4. `loadJobData()`：仅在任务目录就绪（`/progress` 无 `note`）后才请求来源/产物/过程，避免刚提交即 404。
5. 待补充"作为补充资料"把回答**追加**到原 `texts`，不再覆盖提交时粘贴的材料。
6. "配置与评测"新增"安全与数据边界"卡（总计划 2.2：界面需说明发送哪些内容）。
7. `stageLabel()`：任务列表与进度条把 `finished`/`waiting_input`/`evidence` 等内部词转为中文。
8. 后端 `/api/config` 的联网搜索状态改为按模型诊断同口径回落 `.env`（`settings=None` 时不再永远显示"未配置"）。

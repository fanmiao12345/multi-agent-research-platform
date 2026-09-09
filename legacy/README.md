# Agent MVP —— 动手学智能体（最小可运行版）

> 目标：用**约 500 行 Python（零第三方依赖）**，让你亲手搭出一个真正能跑的智能体，
> 看懂它每一行在干什么，然后自己动手改造它。

---

## 1. 先跑起来（2 分钟）

环境要求：Python 3.9+（本机 3.14 已验证），**无需安装任何第三方库**。

**方式 A：一键体验（Windows）**

```bat
双击 run_demo.bat
```

没有 API Key 也没关系：脚本会提示你输入 Key，**直接回车 = 进入离线模拟模式**，
一样能看到完整的智能体循环。

**方式 B：命令行**

```bash
cd agent-mvp

# 没有 Key：自动进入「模拟大脑」模式
python agent.py -q "现在几点了？顺便帮我算一下 12*34 + 56"

# 有 DeepSeek API Key（也兼容其它 OpenAI 兼容接口）
set DEEPSEEK_API_KEY=sk-xxxx          # Windows；Linux/macOS 用 export
python agent.py -q "上海天气怎么样？"

# 连续对话模式（体验跨轮记忆）
python agent.py
```

可选环境变量：`DEEPSEEK_BASE_URL`（默认 https://api.deepseek.com）、`DEEPSEEK_MODEL`（默认 deepseek-chat）。

### 获取并填写 API Key（在哪填、怎么填）

**Key 从哪来**：到 platform.deepseek.com 注册 -> 左侧「API Keys」页面创建 -> 充值少量余额
（一次演示对话通常只要几分钱）。

**运行时不会再弹窗问你要 Key。** 程序按下面优先级读取，全都没有就自动使用离线模拟大脑：

| 优先级 | 位置 | 怎么填 |
|---|---|---|
| 1 | 命令行参数（仅当次生效） | `python agent.py -q "..." --api-key sk-xxxx` |
| 2 | 环境变量 `DEEPSEEK_API_KEY`（正规做法） | 临时：先执行 `set DEEPSEEK_API_KEY=sk-xxxx` 再运行；永久：`setx DEEPSEEK_API_KEY sk-xxxx`（新开窗口生效）；或图形界面：搜「环境变量」-> 新建用户变量 `DEEPSEEK_API_KEY` |
| 3 | **配置文件 `config.ini`（推荐：Key 和模型设置都在一个文件）** | 编辑 `config.ini`，在 `[model]` 区 `api_key =` 后面填上 Key |
| 4 | 代码常量（兜底） | `llm.py` 顶部 `LLM_API_KEY = "sk-xxxx"` |

填好后运行时顶部会打印四行运行信息：**模型 / 接口 / 温度 / Key 来源**，
看到 `当前大脑: LLM` 即已用上真实模型；仍显示 `MockLLM` 说明没读到 Key。

> 第 3、4 种（把 Key 写进 config.ini / 代码）只适合本机学习：Key 会随文件走，
> **不要**把 agent-mvp 文件夹发给别人或上传 Git。正规项目请用第 2 种环境变量。
> 顺带一提：Python 项目常见的 `.env` 文件本质也是"启动时把文件内容读进环境变量"，
> 本 MVP 为保持零依赖没有引入。

### config.ini：一站式配置中心（自己换模型 / 换供应商都改它）

`config.ini` 是本项目推荐的配置入口 —— **改配置不用碰任何代码**：

| 配置项 | 含义 | 示例值 |
|---|---|---|
| `[model] api_key` | API Key（留空则按 环境变量 > llm.py 常量 的顺序找） | `sk-xxxx` |
| `[model] base_url` | 接口地址（OpenAI 兼容，不含 /chat/completions） | `https://api.deepseek.com` |
| `[model] model` | 模型名 | `deepseek-chat` |
| `[model] temperature` | 随机性 0~2，越小越严谨稳定 | `0.7` |
| `[agent] max_rounds` | 普通任务最多「思考+调用工具」轮数 | `8` |
| `[research] max_rounds` | 查资料类任务（deep-research / 流水线研究员）的独立轮数预算 | `20` |
| `[research] parallel` | Fan-out 并行研究员的最大同时数（master 拆子题时生效） | `3` |

改完保存、重新运行即生效。优先级总原则：**命令行参数 > 环境变量 > config.ini > 代码默认**。

几个现成「配方」，复制到 config.ini 对应行即可：

```ini
; 配方 A：DeepSeek V3（默认 —— 工具调用稳定，学本 MVP 用它）
base_url = https://api.deepseek.com
model = deepseek-chat

; 配方 B：OpenAI（Key 换成你自己的）
base_url = https://api.openai.com/v1
model = gpt-4o-mini

; 配方 C：本地 Ollama（免费离线，Key 随便填，如 ollama）
base_url = http://localhost:11434/v1
api_key = ollama
model = qwen2.5:7b        ; 先运行 ollama list 看你机器上有哪些模型
```

注意：`deepseek-reasoner` 这类「推理模型」工具调用支持有限，跑本 MVP 请选
`deepseek-chat`（V3）这类支持 function calling 的模型。

---

## 2. 它跑起来时，到底发生了什么？

先看一次模拟运行的真实输出（对照下面流程图读）：

```
你: 现在几点了？顺便帮我算一下 12*34 + 56 等于多少？
  [第1轮] 大脑决定调用工具: get_time({})
           工具返回: 2026-09-04 13:23:44（Friday）   <- 时间以你运行时为准
  [第1轮] 大脑决定调用工具: calculator({"expression": "12*34 + 56"})
           工具返回: 12*34 + 56 = 464

小智: 工具已返回真实结果，我据此回答：
- 2026-09-04 13:23:44（Friday）
- 12*34 + 56 = 464
```

注意上面第 1 轮里大脑**一次请求了两个工具**——真实 LLM 也会这么做（并行调用）。
主循环逐个执行、把真实结果全部喂回去，大脑看完才给出最终回答。

**这就是智能体最核心的机制 —— ReAct 循环（Reason 思考 + Act 行动 -> Observe 观察）：**

```
                 ┌──────────────────────────────────────────┐
                 │              messages（对话历史）          │
                 │  system / user / assistant / tool 消息     │
                 └──────────────────────────────────────────┘
                                      │  每次把「全部历史 + 工具说明书」发给模型
                                      ▼
   ┌───────────────────────┐   思考    ┌───────────────────────────────┐
   │   主循环 (agent.py)    │◄─────────│   大脑 (llm.py)                │
   │   1. 问大脑下一步怎么做 │          │   真实 LLM：根据上下文决定：    │
   │   2. 是调用工具？还是  │           │    - 调用工具 X(参数)          │
   │      直接回答？        │  ────────►│    - 还是输出最终文本          │
   └──────────┬────────────┘   请求    └───────────────────────────────┘
              │
              │ 决定调用工具时
              ▼
   ┌───────────────────────┐   执行    ┌───────────────────────────────┐
   │  run_tool 按名字找到   │─────────►│   工具 (tools.py)              │
   │  函数并运行（真代码！） │◄─────────│   get_time / calculator /      │
   │  结果转成文本喂回大脑   │  返回结果 │   save_memo / list_memos /     │
   └───────────────────────┘          │   get_weather                  │
                                       └───────────────────────────────┘
```

三个关键认知：

1. **大模型不会执行工具。** 它只会在回复里写一句结构化的话
   `{"name": "calculator", "arguments": "{\"expression\": \"12*34+56\"}"}`，
   真正干活的是你写的主循环。模型负责**决策**，代码负责**执行**。

2. **为什么结果必须由代码执行、再喂回去？** 因为模型会一本正经地「编造」计算结果。
   Agent 的铁律是：*只有工具真实返回的结果才可信*。所以循环是
   「问 -> 干 -> 把真实结果喂回去 -> 再问」，直到模型说出最终答案。

3. **模型没有记忆。** 所谓「记忆」只是把每一轮消息都原样存进 `messages`，
   下次请求时全部发给模型。你打开 `last_transcript.json` 就能看到这份「记忆」的原始形态。

---

## 3. 代码走读（按阅读顺序）

| 文件 | 角色 | 内容 |
|------|------|------|
| `tools.py` | **手** | 7 个工具（时间/计算/备忘/模拟天气 + 联网 web_search/fetch_page）：每个 = 1 个普通 Python 函数 + 1 份 JSON Schema 说明书；末尾的 `TOOL_SCHEMAS` / `TOOL_REGISTRY` / `run_tool()` 是主循环唯一要用的接口 |
| `llm.py` | **大脑** | `LLM`：用标准库 `urllib` 调 DeepSeek 的 OpenAI 兼容接口；`MockLLM`：无 Key 时用 if/else 假装思考，让你离线看清循环 |
| `agent.py` | **循环** | `run_agent()`：ReAct 主循环（全项目最重要的 ~40 行）；`main()`：命令行入口、Key/模型探测、连续对话 |
| `multi.py` | **多智能体** | 4 角色固定流水线（Pipeline），角色人设来自 roles/ 注册表，产物进共享工作区 |
| `master.py` | **主智能体** | 指挥官入口：难度评估 → 单/多路由 → roles 动态组队 → 可 Fan-out 并行研究员 → 逐棒验收（详见第 7 节） |
| `roles/` | **角色注册表** | 每个 .md 一个角色（人设+工具+预算），像 skills/ 一样热插拔；内置 researcher/organizer/writer/editor |
| `roles.py` | 注册表读取 | 现读磁盘加载角色（增删角色不用重启） |
| `workspace.py` | **共享工作区** | 每次运行的"黑板"：产物 .md + state.json（每棒状态/验收结论），为并行与断点续跑打地基 |
| `DEV_PLAN.md` | **开发文档与计划** | 现状盘点、分阶段迭代路线、执行状态总表与工作约定（每步先宣布、后汇报） |
| `config.ini` | **配置中心** | 想自己换模型/供应商？Key、接口地址、模型名、温度、轮数都在这一个文件里改，不用碰代码 |
| `config.py` | 配置读取 | ~30 行：把 config.ini 读成嵌套字典，缺字段用默认值兜底 |
| `skills/` | **技能目录** | 内置 5 个技能，与 multi.py 的角色一一对应：writing-outline（写作策划）/ deep-research（查资料）/ material-organizer（整理素材）/ article-writer（成稿）/ review-editor（审校） |
| `run_demo.bat` | 启动器 | Windows 下一键运行 |
| `web_server.py` | **Web 后端** | 复用本目录全部模块开 HTTP 接口（零第三方依赖），`POST /api/chat` 用 NDJSON 事件流把 ReAct 每一步直播给浏览器；顺带托管 `webui/dist` 构建产物 |
| `run_web.bat` | Web 启动器 | Windows 下一键启动网页界面（`python web_server.py` + 自动开浏览器） |
| `webui/` | **Web 前端** | React + Vite + TypeScript 工程：对话界面 + 工具调用卡片 + 过程时间线（详见第 8 节） |
| `questions.txt` | 练习册 | 12 道循序渐进的问题，用来观察各种行为 |
| `memos.json` | 数据 | 备忘工具持久化到硬盘的「记忆文件」（运行后生成） |
| `last_transcript.json` | 日志 | 最近一次对话的完整 messages，学习利器（运行后生成） |

### 3.1 tools.py —— 一个工具的两半

```python
def calculator(expression: str) -> str:   # 一半：真正的 Python 函数
    ...                                    # 安全解析表达式并计算

TOOL_SCHEMAS = [                            # 另一半：给大模型的「说明书」
    {"type": "function",
     "function": {"name": "calculator",
                  "description": "计算数学表达式，如 '12*34+56' ...",
                  "parameters": {...}}},
]
```

大模型只看得懂「说明书」，只看得见函数名和参数名；它永远看不到你的函数体。
中间由 `run_tool(name, args)` 这座桥连接：名字 -> 查 `TOOL_REGISTRY` -> 执行 -> 返回文本。

顺带两个安全/健壮细节，值得记住：
- 计算器**不用 eval()**，而是把表达式解析成语法树（`ast`），只放行白名单运算——这正是
  ChatGPT Code Interpreter 等产品对用户代码采取同类策略的原因（防止任意代码执行）。
- 任何工具抛异常都会被 `run_tool` 捕获并**转成文本**返回给模型，而不是让程序崩溃。
  模型看到「错误：...」会自己想办法（换参数重试、或如实告诉用户）。

### 3.2 llm.py —— 大脑接口

主循环只认一个方法：`chat(messages, tools) -> {content, tool_calls}`。两种回复：
- `tool_calls` 非空 -> 模型要求调用工具
- 否则 `content` 就是最终答案

`LLM.chat()` 做的事情就是一次 HTTP POST：把 messages 和工具说明书发给
`/chat/completions`，把模型回复里的 `tool_calls` 解析成干净的 dict。
（这里故意用标准库 urllib 而不是 requests/OpenAI SDK，因为**少一个依赖，就少一层黑盒**——
学习阶段值得看穿这个 HTTP 请求。）

`MockLLM` 是一套 if/else 规则：搜关键词 -> 假装返回一次工具调用；看到工具结果 ->
编一段最终回答。**规则就是极简的「模型」**：真实模型是在海量数据上训练出的概率，
Mock 是你写死的规则，但二者驱动的循环一模一样——这就是为什么没有 Key 也能学。

### 3.3 agent.py —— 主循环（核心中的核心）

```python
def run_agent(llm, question, messages=None, max_rounds=8):
    messages.append({"role": "user", "content": question})
    for step in range(1, max_rounds + 1):          # 1. 循环直到收敛
        reply = llm.chat(messages, tools=TOOL_SCHEMAS)   # 2. 问大脑
        if not reply["tool_calls"]:                # 3. 没有工具请求 = 出答案了
            final = reply["content"]; break
        messages.append(assistant_msg)             # 4. 把「调用请求」记入历史
        for call in reply["tool_calls"]:           # 5. 逐个执行（真代码！）
            result = run_tool(call["name"], call["arguments"])
            messages.append({"role": "tool", ...}) # 6. 真实结果喂回去
    return final, messages
```

只做四件事：**问 -> 判断（工具 or 答案）-> 执行 -> 结果回喂**，外加一个 `MAX_ROUNDS`
步数上限防止死循环。历史上 OpenAI 给这个循环起过许多花哨名字
（ReAct / Function Calling Agent / Tool Loop），剥开看都是它。

一个 `messages` 里的角色，值得背下来：

| 角色 | 谁写的 | 作用 |
|------|--------|------|
| `system` | 你 | 给模型定人设和规则（要不要用工具、怎么用） |
| `user` | 你 | 用户问题 |
| `assistant` | 模型 | 模型的「话」（思考说明）或「工具调用请求」 |
| `tool` | 你（代码） | 工具真实执行结果，必须配 `tool_call_id` 对应到某次请求 |

---

## 4. 动手改造：给它加一个新工具（10 分钟）

以「掷骰子」为例，三步即可：

**第 1 步**：在 `tools.py` 加一个普通函数：

```python
import random
def roll_dice(sides: int = 6) -> str:
    """掷一个 sides 面的骰子。"""
    return f"掷出了 {random.randint(1, sides)}（1-{sides} 面骰）"
```

**第 2 步**：在 `TOOL_SCHEMAS` 列表里加说明书（名字、描述、参数必须和函数一致）：

```python
{"type": "function",
 "function": {"name": "roll_dice",
              "description": "掷骰子，返回随机点数。",
              "parameters": {"type": "object",
                             "properties": {"sides": {"type": "integer",
                                                      "description": "骰子面数，默认 6"}}}}},
```

**第 3 步**：在 `TOOL_REGISTRY` 里注册：

```python
"roll_dice": roll_dice,
```

然后运行 `python agent.py -q "掷一个 20 面的骰子"`。如果你有 Key，会发现 DeepSeek
模型无需任何额外训练，立刻就会用新工具——**给 Agent 加能力 = 写函数 + 写说明书 + 注册**，
这就是工具生态（包括 MCP）能无限扩展的根本原因。

> 想给 MockLLM 也加规则？在 `llm.py` 的 `MockLLM.chat` 里加一条 if 即可。

---

## 5. 技能（skill）：给小智装一套「方法论」

工具是「手」，技能是「工作手册」。**技能 = 一段可复用的指令包（markdown），
加载时拼进 system prompt**，让模型按手册规定的方法、步骤、输出格式工作。

内置 5 个技能，正好覆盖 multi.py 流水线的全部角色（单智能体模式下，
小智也能随时扮演其中任意一个角色）：

| 技能 | 对应角色 | 干什么 | 交付物 |
|---|---|---|---|
| `writing-outline` | 策划 | 把模糊想法变成写作方案 | 中心论点 + 大纲 |
| `deep-research` | 研究员 | 拆检索问题、联网查资料（调 web_search/fetch_page） | 《原始资料》事实清单 |
| `material-organizer` | 整理师 | 去重归类、提炼论点 | 《素材包》 |
| `article-writer` | 撰稿人 | 按素材包与要求写成稿 | 成品文章 + 参考来源 |
| `review-editor` | 审校 | 事实/来源/结构/表达四查 | 问题清单或润色稿 |

它们之间通过 `intents`/`avoid_when` 划清了边界（成稿 vs 只出思路、查资料 vs
只整理等），两级路由能稳定区分。使用方式有两种：

**A. 自动路由（默认，推荐）**——你**不用指明技能**，小智自己判断：

```bash
python agent.py
# 然后直接问：我想写一篇公众号文章，主题是坚持健身一年，帮我梳理思路
# 或单次：python agent.py -q "把'远程办公的利与弊'梳理成一篇报告的大纲"
```

流程：每次提问走**两级路由**——
1. **召回**：本地 BM25 检索（`_retrieve_candidates`，零 token、零第三方库）从全部技能里
   粗筛出 top 5 候选。分词用汉字双字词 + 虚词黑名单，避免"帮我/几点"这类误命中；
   `avoid_when` 里写的情形命中请求时会扣分（防「润色」误选「写作梳理」）。
2. **精排**：只把候选清单（名字 + 简介 + 适用/不适用示例）交给模型，让它输出
   `USE_SKILL:名字` 或 `NO_SKILL` —— 候选越少越不容易选错。

命中 -> 自动加载技能正文，控制台打印**命中理由**（`理由: 命中"文章、大纲"`），
模型在回答开头告诉你用了哪个技能；没命中 -> 直接按基础人设回答，技能正文
一个字都不进上下文（省 token）。同技能在同一次会话里**只注入一次**，后续沿用。

**B. 强制指定**——明确要某个技能（自动路由会关闭）：

```bash
python agent.py --skill writing-outline
python agent.py --skill writing-outline -q "我想写一篇关于晨跑的文章，帮我梳理思路"
```

启动横幅会显示当前处于哪种模式：`技能: 自动路由（两级：BM25 粗筛 top5 + 模型精排；现有 N 个技能）`
或 `技能: writing-outline（强制指定，已注入 system prompt）`。

**交互内人工兜底（防"用错技能"的最后防线）**：

| 命令 | 作用 |
|---|---|
| `/skills` | 实时列出技能（含简介/关键词/适用示例） |
| `/use 技能名` | 手动指定：之后每轮直接加载该技能，不再自动路由 |
| `/use` | 查看当前是否手动指定 |
| `/use off` | 恢复自动路由 |

**原理小结**：`_route_skill()` 是精排器（一次额外的轻量模型调用，解析
`USE_SKILL:名字`）；命中后 `_skill_activated_message()` 把技能正文作为一条
system 消息注入。模拟大脑（MockLLM）不做语义精排，直接取检索第一名
（检索本身就是"规则版路由器"，离线也能看到完整两级流程）。

**自己写新技能三步**：
1. 在 `skills/` 下新建 `你的技能名.md`，开头用 `---` 写元信息
   （字段越全，路由越准——尤其 intents 和 avoid_when）：
   ```markdown
   ---
   name: code-review
   description: 一句话说明干什么、什么场景适用（检索与精排都靠它）
   keywords: 代码, 审查, review    # 检索加权词
   intents:                        # 用户的典型说法（检索与精排的"命中范例"）
     - 帮我审查这段代码
     - 这段代码有什么问题
   avoid_when:                     # 什么时候不要用（检索命中会扣分，防误选）
     - 用户只是解释代码，不是要审查
   ---
   ```
2. 正文写清楚：角色 / 触发场景 / 执行步骤 / 输出模板 / 禁止事项（越具体越听话）
3. 运行 `python agent.py` 直接提问命中它；想验证就看看控制台的命中理由、
   回答开头的技能声明，或翻 `last_transcript.json` 里注入的技能 system 消息

改 `writing-outline.md` 的语气、步骤，观察回答变化 —— 这是 prompt 工程最直观的练习，
也是各大 Agent 框架中技能/工具市场的基本原理。技能多了也不怕：两级路由保证
精排模型每次只看 top5 候选；选错了随时 `/use` 纠正。

### 5.1 技能热插拔：增删改技能不用重启

技能是「数据」（`skills/*.md`），不是代码：`_skill_catalog()` **每次提问都重新
扫描磁盘**、`_load_skill()` 命中时才现读文件，从不缓存。所以在正在运行的交互进程里：

- **新增**：往 `skills/` 丢一个 .md（写清 description），下一条提问路由器就可能命中它
- **修改**：改描述/正文/关键词，下一次路由与注入用的就是新内容
- **删除**：移走文件即可，路由器不再命中它
- 随时输入 `/skills` 查看当前实时技能列表（能看到刚加/刚删的）

三个注意点：
1. 热插拔只作用于「自动路由」模式；`--skill xxx` 强制指定是在启动时注入的，
   文件必须在启动前就存在
2. 一次性 `-q` 问答跑完进程就退出了，请用交互模式（`python agent.py`）体验
3. 已注入过对话历史的旧技能正文会残留到会话结束（正常"记忆"现象，介意就重开）
4. 改 `agent.py`/`llm.py` 这类**代码**当然仍要重启 —— 热插拔的对象是技能文件本身

> 不想手动动文件？Web 界面右上角「📚 技能库」可以直接**导入 / 停用 / 删除**技能：
> 粘贴 .md 内容或选文件导入；停用 = 移到 `skills/_disabled/`（不删文件、重启仍保持）；
> 删除 = 永久移除文件。改动下一次提问即生效（详见第 8.2 节）。

## 6. 多智能体流水线：查资料 → 整理 → 写作（multi.py）

把一个主题变成一篇有真实素材支撑的成品，拆给 **4 个各司其职的 Agent** 接力完成
（每个角色 = 独立上下文 + 自己的小循环，产出传给下一位 —— 这就是多智能体
最朴素的「消息传递」形态）：

```
你的一句话主题
   │
   ▼
[1 研究员]  拆检索点 → web_search 多次联网检索 → fetch_page 精读高价值链接
   │         只采信工具真实返回，逐条带来源 → 输出《原始资料》
   ▼
[2 整理师]  去重、归类、提炼 → 《素材包》：3~5 核心论点+证据+来源+数据+缺口
   │
   ▼
[3 撰稿人]  按体裁/读者/字数写成品 v1（文末附真实参考来源）
   │
   ▼
[4 审校]    对照素材查编造/查来源/查结构 → 需修改则撰稿人修订 v2
   │
   ▼
research_output/<时间戳_主题>/  00_task.md … 04_review.md + final.md
```

**运行**（三种姿势）：

```bash
python multi.py                        # 向导式：一步步问你主题/体裁/读者/字数
python multi.py "深空探测对人类的意义"   # 直接开跑（默认公众号推文/普通大众/1200字）
python multi.py "主题" --kind 研究报告 --audience 行业从业者 --words 2000 --extra 要数据和反方观点
```

**要点**：
- 研究员是唯一带工具的角色（`web_search`/`fetch_page` 已加入 `tools.py`，真实联网，
  优先 Bing、失败退回 DuckDuckGo；抓取只读公网，自动拦内网地址防 SSRF）；
  其余角色是「纯脑力」工人，只做单轮推理 —— 用最少 token 完成最多分工
- 没有 Key 时 `--force-mock` 也能跑通整条流水线看机制（研究员照样真联网；
  整理/写作是占位文本）。想要真实成稿请配置 Key（config.ini）
- 每次运行产出独立时间戳文件夹，中间产物全部留档：任务书、原始资料、素材包、
  成稿 v1、审校意见、终稿 —— 方便你复盘"哪一棒出问题"
- 零依赖方案用 HTML 抓取搜索引擎，稳定性和合规性都有限（可能被限流/改版）；
  正式项目应换成搜索 API/网页正文服务 —— 这也是 RAG 技术栈的雏形，
  学会了它，再看 LangGraph/CrewAI 的多 Agent 编排就是小菜

## 7. 主智能体调度：难度评估 + 挂帅指挥（master.py）

`multi.py` 是**代码写死的固定流水线**；`master.py` 则多了一个「主智能体」——
一个真正在指挥的 LLM 决策者，掌握整体节奏与结果判断：

```
你的一句话任务
    │
    ▼
[主智能体·评估] 难度与复杂度 ──┬─ MODE: single ──▶ 亲自上场（=agent.py 单智能体：工具+技能自动路由）
    │   （REASON + STEPS）    └─ MODE: multi  ──▶ 挂帅调度，逐棒委派：
    ▼
  [主智能体·派工] 委派子智能体（researcher→organizer→writer→editor 或其子集）
    ▼
  [子智能体] 用自己的角色人设独立跑一轮（研究员带工具、20 轮预算，其余纯脑力）
    ▼
  [主智能体·验收] 对照总任务验收产物，四选一：
      PASS   → 过关，委派下一棒
      REWORK → 同一子智能体带具体意见返工（同角色最多 2 次）
      ADD    → 临时加派 researcher 补查缺口，插队继续
      FINAL  → 满意，收尾并交付最终产出
```

- 主智能体与子智能体是**两层结构**：主控的每段决策是一次独立小调用（评估 1 次 +
  每棒验收 1 次，token 开销很小）；真正干活的是被委派的子智能体（复用 multi.py
  的角色人设与工人循环，代码零重复）
- **任务分级是自动的**：主智能体判断"查资料/多步研究/成稿"类复杂任务才走 MULTI；
  日常问答走 SINGLE（省时省钱）。可用 `--force-single` / `--force-multi` 手动覆盖
- 防失控保险：委派总次数上限与返工上限（master.py 顶部 `MAX_STEPS`/`MAX_REWORK`），
  子智能体内部仍各带轮数预算与触顶收尾
- 离线（`--force-mock`）时主控的"评估/验收"由代码关键词规则兜底，流程照样可看

```bash
python master.py "帮我调研一下深空探测的意义，写一篇公众号文章"
python master.py "帮我审校这段文字" --kind 报告    # 复杂任务可附带成稿参数
python master.py                                  # 交互模式：每句话先过主控评估
```

与技能路由的关系：技能路由器（agent.py 内）是"问题级"决策（这道题要不要套方法论），
主智能体是"任务级"决策（这个任务要不要开多智能体、怎么派工）——两个层级不冲突，
SINGLE 路线内部照样走技能自动路由。

### 7.1 已落地的四种协作模式

对照常见的多智能体模式清单，本项目现在的组合是：

| 模式 | 落地位置 | 说明 |
|---|---|---|
| **Role-based** | `roles/` 注册表 + `roles.py` | 角色 = 数据文件（frontmatter 声明 use_tools/budget + 人设正文），**新增角色 = 丢一个 .md**，运行中热插拔；multi/master 都从注册表读，不再写死 |
| **Pipeline** | `multi.py` | 保留固定四棒作为"确定性快通道" |
| **Manager–Worker + Dynamic Team** | `master.py` | 主控从注册表组队（STEPS 任选角色子集），逐棒验收（PASS/REWORK/ADD/FINAL），ADD 可临时插队补查 |
| **Fan-out / Fan-in** | `master.py` 研究员步骤 | 主控拆 2~3 个独立子题 → 并行 N 个研究员（线程并发，各自独立上下文与预算）→ 主控验收合并产物 → 才交给下一棒；并行数在 `config.ini` 的 `[research] parallel` |
| **Handoff** | `master.py` 派工单 | 每棒任务开头带【交接卡】：输入来自哪个文件、上一棒验收状态——后手知道素材可信度 |
| **Blackboard / Shared Workspace** | `workspace.py` | 每次运行 = 一个目录：产物 .md + `state.json` 黑板（每步角色/文件/验收/反馈/预览），扇入合并、失败排查、断点续跑的地基 |

角色（roles/）与技能（skills/）的分工：**角色 = 流水线里的工种**（谁上场、带不带工具），
**技能 = 单智能体的问题级方法论**（同一个人怎么干活）。两者都是 md 数据、都能热插拔。

## 8. Web 界面：在浏览器里看小智干活（可选）

命令行之外，本项目还带一个**网页聊天界面**：对话 + ReAct 过程可视化 ——
技能命中、每一轮的思考、工具调用（参数 + 真实返回）、最终答案都实时画在页面上。
跑的是**同一条 `run_agent()` 主循环**，不是另写的玩具。

### 8.1 快速启动

**方式 A：终端（推荐）**——在 agent-mvp 目录下执行：

```bash
python web_server.py                  # 启动后浏览器访问 http://127.0.0.1:8765
python web_server.py --force-mock     # 离线模拟大脑（不想花 token 时）
python web_server.py --skill deep-research   # 强制某个技能（关闭自动路由）
```

> 服务是阻塞式运行：占住当前终端，Ctrl+C 停止；这个终端保持开着时，
> 想同时跑别的事请另开一个终端。

**方式 B：双击脚本**（Windows 可选）——`run_web.bat`，等价于方式 A 第一条。

> 后端零第三方依赖（Python 标准库 `http.server`）。前端是独立工程：
> `webui/`（React + Vite + TypeScript，仅 UI 部分用现代工具链）。

### 8.2 两个组件怎么配合

| 组件 | 位置 | 职责 |
|---|---|---|
| 后端 `web_server.py` | agent-mvp 根目录 | 复用 `agent.py`/`llm.py`/`tools.py`，开 HTTP 接口；托管前端构建产物 |
| 前端 `webui/` | 独立工程 | React 界面；开发期 `npm run dev`（5173，/api 代理到 8765），生产用 `npm run build` |

**过程是怎么「直播」出来的？** `run_agent()` 新增了一个可选回调 `emit(...)`：
每发生一件值得看的事（命中技能 / 大脑决定调工具 / 工具返回 / 出答案）就发一个
带 `type` 的小字典 —— 命令行下没人传它，行为完全不变；Web 后端把它逐行写成
NDJSON 事件流（`POST /api/chat`），浏览器读到一行渲染一行：

```
{"t":"boot","brain":"LLM",...}       会话开始 + 引擎信息
{"t":"skill",...}                    技能路由命中
{"t":"round","round":1,...}          第 1 轮：大脑的话 + 想调的工具
{"t":"tool","name":"calculator",...} 开始执行工具
{"t":"tool_result","result":"..."}   工具真实返回（只展示真结果，不展示编造）
{"t":"answer","content":"..."}       最终答案
{"t":"done"}                         本次任务结束
```

**记忆在服务端**：每个会话（浏览器生成的 sid）对应一份对话历史，下一问自动带上，
和 `python agent.py` 连续对话模式的机制一模一样；历史存在内存里，重启后端即清空。

### 8.3 前端自己改 / 自己构建（熟悉 Vite 的同学）

```bash
cd webui
npm install
npm run dev        # 开发模式：http://127.0.0.1:5173（/api 自动代理到 8765）
npm run build      # 产物进 webui/dist，之后 python web_server.py 直接托管
```

### 8.4 常见问题

- **改了 config.ini 不生效？** Web 后端启动时读一次配置，改完请重启
  `python web_server.py`（界面右上角有「config.ini」提示）。
- **显示「MockLLM 离线模拟」？** 没读到 API Key —— 和命令行一样自动降级了，
  界面流程照样完整；填 Key（config.ini 或环境变量 `DEEPSEEK_API_KEY`）后重启即可。
- **浏览器能展示哪一步最值得看？** 工具卡片。注意它只展示 `run_tool()`
  真实执行返回的结果 —— 这正是「只有工具返回才可信」这条 Agent 铁律的直观呈现。
- **页面右上角「＋新对话」** = 换个新 sid，同时通知后端忘掉旧会话历史。
- **想导入 / 停用 / 删除技能？** 点右上角「📚 技能库」：粘贴 .md 内容或从本机读
  .md 文件导入；「停用」只是把文件移进 `skills/_disabled/`（可随时启用、重启后仍
  保持）；「删除」永久移除文件。全部改动下一次提问即生效，无需重启后端。

## 9. 沿着代码看出去的「下一步」（学习路线图）

跑通 MVP 后，真正的智能体还差这几块，按性价比排序：

1. **更强的记忆**：现在对话一多，历史会超长。读一读「RAG / 向量检索」，让 Agent 从
   外部文档里按需检索知识——记忆从此不再受限于上下文窗口。
2. **规划能力**：复杂任务拆步骤。让模型先输出 plan 再执行（Plan-and-Execute）；
   给循环加「反思」：出错了让它读错误信息自己修正（Self-Refine）。
3. **MCP（Model Context Protocol）**：我们的 `TOOL_SCHEMAS + TOOL_REGISTRY` 模式，
   正是 MCP 要标准化的东西——统一工具描述与调用协议，让任何 Agent 即插即用地连上
   任何工具/数据源。理解了本项目，MCP 对你就是「注册表 + 网络协议」两个词。
4. **框架**：看过裸实现后再去读 LangGraph / OpenAI Agents SDK / 各类 Agent 框架，
   你会发现它们只是把这个循环工程化（状态机、并行、重试、可观测性），原理你已经懂了。
5. **多 Agent 协作**：多个角色（规划者/执行者/审查者）各跑一个循环、互相发消息——
   本质是把「一个大脑」换成「一组各司其职的大脑」。

**建议学习顺序**：先改工具（第 4 节）-> 再读 `last_transcript.json` 研究真实消息流 ->
打开 DeepSeek 官方文档看 function calling 一节 -> 然后去碰框架或 MCP。

---

## 10. FAQ

**Q：模拟模式和真模型有什么区别？**
模拟模式（MockLLM）用 if/else 关键词规则假装思考，只覆盖预置问题，胜在免费、
离线、可复现；真模型能理解任意表述、多步规划、甚至自己发现需要用哪个工具。
两者跑的是同一个 `agent.py` 循环，所以你学会的循环知识 100% 可迁移。

**Q：为什么用 urllib 不用 requests / openai SDK？**
学习阶段减少依赖 = 减少黑盒。正式项目里当然可以用 SDK，那时你已经知道它封装的是什么。

**Q：agent.py 交互模式里我上次说的它还「记得」吗？**
记得。`history` 被持续传给下一轮 `run_agent`，这就是对话记忆。注意区分：
对话记忆（messages）和长期记忆（memos.json 这类工具读写的外部存储）是两回事，
前者重启进程即失，后者持久化在硬盘。

**Q：为什么模型有时不用工具、直接回答计算题？**
小算式模型自己能算（虽然可能算错），大算式或精确计算它应该调用 calculator——
你可以改 `agent.py` 里 `SYSTEM_PROMPT` 的措辞来调教它，这也是 Agent 开发日常：
**调 Prompt 就是调行为**。

**Q：文件里的时间/天气准吗？**
`get_time` 是真实的本地时间。`get_weather` 是模拟数据（代码注释里写明了），
把它换成真实天气 API 正是第 4 节「加新工具」的现成练习。

**Q：报错「HTTP 401：api key ... invalid」怎么办？**
说明当前 Key 无效（过期 / 填错 / 被禁用 / 复制不完整）。去 platform.deepseek.com
-> 左侧「API Keys」->「创建新 Key」-> 复制 -> 填进 `config.ini` 的 `api_key =`
（或环境变量 `DEEPSEEK_API_KEY`），保存重跑。注意：新 Key 只在创建那一刻**完整显示一次**，
离开页面就看不到了，只能重建。
**先自检再问答**：填好 Key 后运行 `python agent.py --check-key`，它会连一次接口
（不消耗 token），通过会列出可用模型，失败会直接告诉你是 Key 的问题还是接口地址的问题，
不用等问答时才发现 401。

// 前后端共用的数据形状（与 web_server.py 的 NDJSON 事件一一对应）

export interface SkillMeta {
  name: string
  description: string
}

/** 技能库条目（含停用状态；停用 = 文件被移入 skills/_disabled/） */
export interface SkillInfo {
  name: string
  description: string
  keywords: string[]
  intents: string[]
  avoid_when: string[]
  enabled: boolean
}

/** 引擎状态：相当于 CLI 启动横幅的 JSON 版 */
export interface EngineState {
  brain: 'LLM' | 'MockLLM' // MockLLM = 离线模拟大脑
  model: string
  base_url: string
  temperature: number
  key_source: string | null
  key_configured: boolean
  max_rounds: number
  research_max_rounds: number
  forced_skill: string | null
  skills: SkillMeta[]
  version: string
}

export interface ToolCallInfo {
  id: string
  name: string
  arguments: Record<string, unknown>
}

// ---- 后端推来的事件流（NDJSON，一行一个）----
export interface BootEv extends EngineState {
  t: 'boot'
}
export interface SkillEv {
  t: 'skill'
  name: string
  origin: string
  reason?: string
  state: 'loaded' | 'active'
}
export interface NoticeEv {
  t: 'notice'
  level: 'info' | 'warn' | 'error'
  message: string
}
export interface RoundEv {
  t: 'round'
  round: number
  content: string | null // 大脑本轮说的话（思考/说明），可能没有
  tool_calls: ToolCallInfo[] // 它同时请求调用的工具
}
export interface ToolEv {
  t: 'tool'
  round: number
  id: string
  name: string
  arguments: Record<string, unknown>
}
export interface ToolResultEv {
  t: 'tool_result'
  round: number
  id: string
  name: string
  result: string
}
export interface AnswerEv {
  t: 'answer'
  content: string
}
export interface ErrorEv {
  t: 'error'
  message: string
}
export interface DoneEv {
  t: 'done'
}
export type Ev =
  | BootEv
  | SkillEv
  | NoticeEv
  | RoundEv
  | ToolEv
  | ToolResultEv
  | AnswerEv
  | ErrorEv
  | DoneEv

// ---- 前端展示模型：一个「回合」= 你的一次提问 + 小智完整跑完的过程 ----
export type Step =
  | { kind: 'skill'; name: string; origin: string; reason?: string; loaded: boolean }
  | { kind: 'notice'; level: NoticeEv['level']; message: string }
  | { kind: 'think'; round: number; content: string }
  | {
      kind: 'tool'
      key: string // round:id —— 与后端工具事件配对用
      round: number
      name: string
      arguments: string // 格式化后的参数文本
      status: 'pending' | 'running' | 'done'
      result?: string
    }

export interface Turn {
  qid: string // 本回合唯一 id
  question: string
  steps: Step[]
  answer: string | null
  error: string | null
  status: 'running' | 'done' | 'error'
}

// 与后端 web_server.py 通信：/api/state（引擎状态）+ /api/chat（NDJSON 事件流）
import type { EngineState, Ev, SkillInfo } from './types'

export async function getEngine(): Promise<EngineState> {
  const resp = await fetch('/api/state')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return (await resp.json()) as EngineState
}

export function resetServerSession(sid: string): void {
  // 新对话时通知后端可以丢掉旧历史（失败无所谓，服务端有上限自动清理）
  fetch('/api/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sid }),
  }).catch(() => undefined)
}

// ---------------- 技能库管理 ----------------

export interface SkillActionResult {
  ok: boolean
  message: string
}

async function skillRequest(path: string, body: unknown): Promise<SkillActionResult> {
  let resp: Response
  try {
    resp = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    return { ok: false, message: '无法连接后端' }
  }
  const data = (await resp.json().catch(() => ({}))) as {
    ok?: boolean
    message?: string
    error?: string
  }
  if (!resp.ok || data.ok === false) {
    return { ok: false, message: data.error ?? `HTTP ${resp.status}` }
  }
  return { ok: true, message: data.message ?? '' }
}

export async function fetchSkills(): Promise<SkillInfo[]> {
  const resp = await fetch('/api/skills')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const data = (await resp.json()) as { skills: SkillInfo[] }
  return data.skills
}

/** 导入技能：name 为空时后端会尝试从 frontmatter 的 name: 提取 */
export const importSkill = (name: string, content: string) =>
  skillRequest('/api/skills', { name, content })
/** 停用（enable=false）= 移入 skills/_disabled/，文件不删除 */
export const toggleSkill = (name: string, enable: boolean) =>
  skillRequest('/api/skills/toggle', { name, enable })
/** 删除技能（不可恢复） */
export const deleteSkill = (name: string) => skillRequest('/api/skills/delete', { name })

/** 解析一行 NDJSON 事件；解析失败返回 null（忽略脏行） */
export function parseEvent(line: string): Ev | null {
  if (!line.trim()) return null
  try {
    const obj = JSON.parse(line) as Ev
    if (typeof obj === 'object' && obj && typeof (obj as { t?: unknown }).t === 'string') {
      return obj
    }
    return null
  } catch {
    return null
  }
}

/**
 * 发起一次提问，逐个回调后端推来的事件（阻塞直到 done / 出错 / 连接断开）。
 * 返回 null 表示流程正常结束；返回字符串表示连接层面的错误信息。
 */
export async function streamChat(
  sid: string,
  question: string,
  onEvent: (ev: Ev) => void,
): Promise<string | null> {
  let resp: Response
  try {
    resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid, question }),
    })
  } catch {
    return '无法连接到后端（python web_server.py 是否在运行？）'
  }
  if (resp.status === 409) return '该会话正在运行中，请稍候再试'
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    return `后端返回 HTTP ${resp.status}：${text.slice(0, 200)}`
  }
  if (!resp.body) return '浏览器不支持流式读取'

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl = buffer.indexOf('\n')
      while (nl >= 0) {
        const line = buffer.slice(0, nl).trim()
        buffer = buffer.slice(nl + 1)
        const ev = parseEvent(line)
        if (ev) onEvent(ev)
        nl = buffer.indexOf('\n')
      }
    }
  } catch {
    return '连接中断（后端可能已停止，或请求超时）'
  } finally {
    reader.releaseLock()
  }
  return null
}

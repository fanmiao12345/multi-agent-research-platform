import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { EngineState, Ev, Step, Turn } from './types'
import { getEngine, resetServerSession, streamChat } from './api'
import { fmtArgs } from './toolMeta'
import { TurnView } from './components/TurnView'
import SkillsManager from './components/SkillsManager'

const SAMPLES = [
  '现在几点了？顺便帮我算 12*34 + 56',
  '北京天气怎么样？',
  '帮我记住：周五下午 3 点开周会',
  '我记过什么备忘？',
]

function newQid(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [engine, setEngine] = useState<EngineState | null>(null)
  const [engineError, setEngineError] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [running, setRunning] = useState(false)
  const [input, setInput] = useState('')
  const [skillsOpen, setSkillsOpen] = useState(false)

  const sidRef = useRef<string>(newQid())
  const runningRef = useRef(false)
  const chatRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // ---- 引擎状态（启动时拉一次；每次对话的 boot 事件也会顺带刷新）----
  const refreshEngine = useCallback(() => {
    getEngine().then(
      (st) => {
        setEngine(st)
        setEngineError(null)
      },
      () => undefined,
    )
  }, [])
  useEffect(() => {
    void getEngine().then(
      (st) => {
        setEngine(st)
        setEngineError(null)
      },
      () => setEngineError('无法连接后端'),
    )
  }, [])

  // ---- 自动滚动：发送/结束时贴底；运行中用户往上翻则不打扰 ----
  useEffect(() => {
    const el = chatRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [turns, running])

  const onScroll = useCallback(() => {
    const el = chatRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140
  }, [])
  useEffect(() => {
    const el = chatRef.current
    el?.addEventListener('scroll', onScroll, { passive: true })
    return () => el?.removeEventListener('scroll', onScroll)
  }, [onScroll])

  // ---- 回合更新小工具 ----
  const patchTurn = useCallback((qid: string, fn: (t: Turn) => Turn) => {
    setTurns((ts) => ts.map((t) => (t.qid === qid ? fn(t) : t)))
  }, [])
  const addStep = useCallback(
    (qid: string, step: Step) => patchTurn(qid, (t) => ({ ...t, steps: [...t.steps, step] })),
    [patchTurn],
  )

  /** 把后端事件归约进当前回合的展示状态 */
  const onEvent = useCallback(
    (qid: string, ev: Ev) => {
      switch (ev.t) {
        case 'boot':
          setEngine({
            brain: ev.brain,
            model: ev.model,
            base_url: ev.base_url,
            temperature: ev.temperature,
            key_source: ev.key_source,
            key_configured: ev.key_configured,
            max_rounds: ev.max_rounds,
            research_max_rounds: ev.research_max_rounds,
            forced_skill: ev.forced_skill,
            skills: ev.skills,
            version: ev.version,
          })
          break
        case 'skill':
          addStep(qid, {
            kind: 'skill',
            name: ev.name,
            origin: ev.origin,
            reason: ev.reason,
            loaded: ev.state === 'loaded',
          })
          break
        case 'notice':
          addStep(qid, { kind: 'notice', level: ev.level, message: ev.message })
          break
        case 'round':
          if (ev.content) {
            addStep(qid, { kind: 'think', round: ev.round, content: ev.content })
          }
          for (const c of ev.tool_calls) {
            addStep(qid, {
              kind: 'tool',
              key: `${ev.round}:${c.id}`,
              round: ev.round,
              name: c.name,
              arguments: fmtArgs(c.arguments),
              status: 'pending',
            })
          }
          break
        case 'tool':
          patchTurn(qid, (t) => ({
            ...t,
            steps: t.steps.map((s) =>
              s.kind === 'tool' && s.key === `${ev.round}:${ev.id}`
                ? { ...s, status: 'running' }
                : s,
            ),
          }))
          break
        case 'tool_result':
          patchTurn(qid, (t) => ({
            ...t,
            steps: t.steps.map((s) =>
              s.kind === 'tool' && s.key === `${ev.round}:${ev.id}`
                ? { ...s, status: 'done', result: ev.result }
                : s,
            ),
          }))
          break
        case 'answer':
          patchTurn(qid, (t) => ({ ...t, answer: ev.content }))
          break
        case 'error':
          patchTurn(qid, (t) => ({ ...t, error: ev.message, status: 'error' }))
          break
        case 'done':
          patchTurn(qid, (t) => ({ ...t, status: t.status === 'error' ? 'error' : 'done' }))
          break
      }
    },
    [addStep, patchTurn],
  )

  const sendQuestion = useCallback(
    async (raw: string) => {
      const question = raw.trim()
      if (!question || runningRef.current) return
      const qid = newQid()
      runningRef.current = true
      stickRef.current = true
      setRunning(true)
      setInput('')
      setTurns((ts) => [
        ...ts,
        { qid, question, steps: [], answer: null, error: null, status: 'running' },
      ])
      const err = await streamChat(sidRef.current, question, (ev) => onEvent(qid, ev))
      if (err) patchTurn(qid, (t) => ({ ...t, error: err, status: 'error' }))
      runningRef.current = false
      stickRef.current = true
      setRunning(false)
    },
    [onEvent, patchTurn],
  )

  const newChat = useCallback(() => {
    resetServerSession(sidRef.current) // 让后端忘掉旧会话的历史
    sidRef.current = newQid()
    setTurns([])
    stickRef.current = true
  }, [])

  const autosize = useCallback(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = '0px'
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [])

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // 回车发送；Shift+Enter 换行；中文输入法组词中不触发
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault()
        void sendQuestion(input)
      }
    },
    [input, sendQuestion],
  )

  const mock = engine?.brain === 'MockLLM'
  const canSend = input.trim().length > 0 && !running && engineError === null

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="whale">🐳</span>
          <div className="brand-text">
            <h1>
              小智 <em>· Agent MVP Web</em>
            </h1>
            <p>ReAct 循环可视化 —— 每步思考与工具调用都实时可见</p>
          </div>
        </div>
        <div className="top-actions">
          {engine && (
            <>
              <span className={`pill brain ${mock ? 'mock' : 'llm'}`} title="后端进程实际使用的「大脑」">
                🧠 {mock ? 'MockLLM 离线模拟' : 'LLM 真实模型'}
              </span>
              {!mock && <span className="pill">{engine.model}</span>}
              <span className="pill">温度 {engine.temperature}</span>
              <span className="pill" title="改 config.ini 后重启 python web_server.py 生效">
                ⚙ config.ini
              </span>
            </>
          )}
          {engine && (
            <button
              className="btn ghost"
              onClick={() => setSkillsOpen(true)}
              title="查看 / 导入 / 停用 / 删除技能（skills/ 目录，改动下一次提问即生效）"
            >
              📚 技能库
            </button>
          )}
          <button className="btn primary" onClick={newChat}>
            ＋ 新对话
          </button>
        </div>
      </header>

      <main className="chat" ref={chatRef}>
        <div className="chat-col">
          {engineError && (
            <div className="errbox standalone">
              无法连接后端 —— 请先到 agent-mvp 目录运行
              <code>python web_server.py</code>
              <button
                className="btn ghost small"
                onClick={() => {
                  setEngineError(null)
                  void getEngine().then(
                    (st) => {
                      setEngine(st)
                      setEngineError(null)
                    },
                    () => setEngineError('无法连接后端'),
                  )
                }}
              >
                重试
              </button>
            </div>
          )}

          {turns.length === 0 && !engineError && (
            <div className="empty">
              <div className="empty-whale">🐳</div>
              <h2>把命令行里的小智，搬进浏览器</h2>
              <p>
                这里跑的是 <b>agent-mvp</b> 同一条 ReAct 主循环：每轮「大脑思考 →
                调用工具 → 观察真实结果 → 再思考」都会实时画出来。
                它记得本会话聊过什么 —— 多轮提问会带上前面的历史。
              </p>
              {engine && (
                <div className="empty-meta">
                  {mock ? (
                    <span className="mocktag">
                      当前是<strong>离线模拟大脑</strong>（没配 API Key，不花 token）——
                      问个时间/计算/备忘试试完整流程。
                    </span>
                  ) : (
                    <span className="llmtag">已连真实模型（{engine.model}），直接开聊。</span>
                  )}
                  <span className="skilltags">
                    内置技能：
                    {engine.skills.map((s) => (
                      <code key={s.name} title={s.description}>
                        {s.name}
                      </code>
                    ))}
                  </span>
                </div>
              )}
              <div className="samples">
                {SAMPLES.map((s) => (
                  <button
                    key={s}
                    className="chip"
                    onClick={() => {
                      setInput(s)
                      taRef.current?.focus()
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t) => (
            <TurnView key={t.qid} turn={t} />
          ))}

          {running && (
            <div className="thinking">
              <span className="th-whale">🐳</span>
              <span className="th-text">小智工作中…</span>
              <span className="dots">
                <i />
                <i />
                <i />
              </span>
            </div>
          )}
          <div className="spacer" />
        </div>
      </main>

      <footer className="dock">
        <div className="dock-inner">
          {engine && mock && (
            <div className="mockbar">
              🧠 离线模拟大脑运行中 —— 想体验真实模型：把 Key 填进
              <code>config.ini</code>
              重启 <code>python web_server.py</code>（或不带 Key 继续零成本体验）
            </div>
          )}
          {engine && !mock && (
            <div className="llmbar">
              <span className="dot-green" />
              {engine.model} · {engine.base_url} · Key 来源：
              {engine.key_source ?? 'config.ini'}
            </div>
          )}
          <div className="composer">
            <textarea
              ref={taRef}
              value={input}
              rows={1}
              placeholder={
                running
                  ? '小智正在工作中…可以先把下一句话打好（任务结束后再发送）'
                  : '问问小智：几点啦？算个数？记个备忘？聊点什么都行…'
              }
              onChange={(e) => {
                setInput(e.target.value)
                autosize()
              }}
              onKeyDown={onKeyDown}
              disabled={!!engineError}
            />
            <button
              className="btn send"
              disabled={!canSend}
              onClick={() => void sendQuestion(input)}
              title="发送（Enter）"
            >
              ➤
            </button>
          </div>
          <div className="hintline">
            Enter 发送 · Shift+Enter 换行 · 工具真实返回才会展示 · 刷新页面 = 新会话（服务端历史在内存中）
          </div>
        </div>
      </footer>

      {skillsOpen && (
        <SkillsManager onClose={() => setSkillsOpen(false)} onChanged={refreshEngine} />
      )}
    </div>
  )
}

import { memo } from 'react'
import type { Step, Turn } from '../types'
import { Markdown } from './Markdown'
import { ToolCard } from './ToolCard'

function StepItem({ step, prevToolRound }: { step: Step; prevToolRound: number }) {
  if (step.kind === 'skill') {
    return (
      <div className={`step-skill ${step.loaded ? 'loaded' : 'active'}`}>
        <span className="step-emoji">📘</span>
        <span className="step-main">
          技能路由 · {step.loaded ? '命中并加载' : '沿用已生效'}「{step.name}」
          {step.reason ? <em className="muted">　（理由：命中“{step.reason}”）</em> : null}
        </span>
        <span className="muted step-ori">{step.origin}</span>
      </div>
    )
  }
  if (step.kind === 'notice') {
    const icon = step.level === 'warn' ? '⚠️' : step.level === 'error' ? '🚫' : 'ℹ️'
    return (
      <div className={`step-notice lv-${step.level}`}>
        <span className="step-emoji">{icon}</span>
        <span>{step.message}</span>
      </div>
    )
  }
  if (step.kind === 'think') {
    return (
      <div className="step-think">
        <span className="round-tag">第 {step.round} 轮</span>
        <span className="think-text">💭 {step.content}</span>
      </div>
    )
  }
  // tool
  const firstOfRound = step.round !== prevToolRound
  return (
    <>
      {firstOfRound && (
        <div className="roundsep">
          <span>第 {step.round} 轮 · 大脑决定调用工具</span>
        </div>
      )}
      <ToolCard step={step} />
    </>
  )
}

/** 一个回合：你的提问 + 小智的 ReAct 全过程（技能命中 → 逐轮思考 → 工具调用 → 最终答案） */
export const TurnView = memo(function TurnView({ turn }: { turn: Turn }) {
  let prevToolRound = -1
  return (
    <section className={`turn st-${turn.status}`} aria-busy={turn.status === 'running'}>
      <div className="qrow">
        <span className="qavatar">你</span>
        <div className="qtext">{turn.question}</div>
      </div>

      <div className="arow">
        <span className="aavatar">🐳</span>
        <span className="aname">小智</span>
        {turn.status === 'running' && <span className="arunning">运行中…</span>}
      </div>

      <div className="steps">
        {turn.steps.map((s, i) => {
          const el = (
            <StepItem
              key={i}
              step={s}
              prevToolRound={s.kind === 'tool' ? prevToolRound : -1}
            />
          )
          if (s.kind === 'tool') prevToolRound = s.round
          return el
        })}
      </div>

      {turn.answer !== null && (
        <div className="answer">
          <Markdown>{turn.answer}</Markdown>
        </div>
      )}

      {turn.error && (
        <div className="errbox">
          <b>出错了：</b>
          {turn.error}
          <div className="muted errhint">
            提示：Key 无效 / 接口地址不对 / 断网都会导致真实模型调用失败；可先跑
            python web_server.py --force-mock 验证界面本身。
          </div>
        </div>
      )}
    </section>
  )
})

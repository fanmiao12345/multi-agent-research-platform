import { memo, useState } from 'react'
import type { Step } from '../types'
import { toolMeta } from '../toolMeta'

/**
 * 一个工具调用卡片：名字 + 参数（可展开的代码）+ 状态，执行完附上真实返回。
 * 状态流转：pending（大脑刚请求）-> running（正在执行）-> done（拿到真实结果）。
 * 只有工具真实返回的结果才可信 —— 界面和主循环一样只展示「真结果」。
 */
export const ToolCard = memo(function ToolCard({ step }: { step: Extract<Step, { kind: 'tool' }> }) {
  const meta = toolMeta(step.name)
  const short = (step.result ?? '').split('\n').length <= 5 && (step.result?.length ?? 0) <= 500
  const [open, setOpen] = useState(short)
  const statusText =
    step.status === 'pending' ? '等待执行' : step.status === 'running' ? '执行中…' : '已完成'

  return (
    <div className={`toolcard ${step.status}`}>
      <div className="toolcard-head">
        <span className="tool-icon">{meta.icon}</span>
        <span className="tool-name">{meta.label}</span>
        <code className="tool-fn">{step.name}()</code>
        {meta.blurb && <span className="tool-blurb">{meta.blurb}</span>}
        <span className={`tool-status st-${step.status}`}>
          {step.status === 'running' && <span className="spinner" />}
          {statusText}
        </span>
      </div>

      <div className="tool-args">
        <span className="tool-args-label">参数</span>
        <pre className="args-pre">{step.arguments}</pre>
      </div>

      {step.result !== undefined && (
        <details className="tool-result" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
          <summary>
            {open ? '收起返回结果' : '展开返回结果'}
            <span className="result-len">（{step.result.length} 字符）</span>
          </summary>
          <pre className="result-pre">{step.result}</pre>
        </details>
      )}
    </div>
  )
})

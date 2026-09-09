import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import type { SkillInfo } from '../types'
import { deleteSkill, fetchSkills, importSkill, toggleSkill } from '../api'

interface Props {
  onClose: () => void
  onChanged: () => void // 有任何增删改后通知 App 刷新引擎状态（右上角数量/首页技能列表）
}

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/

/** 从技能内容里提取 frontmatter 的 name: 字段 */
function extractName(text: string): string {
  const m = /^name\s*:\s*([A-Za-z0-9_-]+)/m.exec(text)
  return m ? m[1] : ''
}

/**
 * 技能库管理面板：导入（粘贴 / 选 .md 文件）、停用/启用、删除。
 * 原理：技能就是 skills/ 目录下的 .md「数据文件」—— 本面板只是对文件做
 * 增 / 移动 / 删，主循环每次提问都现扫磁盘，所以改动下一次提问立即生效
 * （热插拔），不用重启后端，也不影响命令行用法。
 */
export default function SkillsManager({ onClose, onChanged }: Props) {
  const [list, setList] = useState<SkillInfo[] | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const [importing, setImporting] = useState(false)
  const [busyName, setBusyName] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(async () => {
    setList(await fetchSkills().catch(() => null))
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])

  async function doImport() {
    if (!content.trim()) {
      setMsg({ ok: false, text: '技能内容为空 —— 先粘贴内容或选择一个 .md 文件' })
      return
    }
    if (!NAME_RE.test(name)) {
      setMsg({
        ok: false,
        text: '技能名只能由字母/数字/_/- 组成，且以字母或数字开头',
      })
      return
    }
    setImporting(true)
    setMsg(null)
    const r = await importSkill(name, content)
    setMsg({ ok: r.ok, text: r.message })
    if (r.ok) {
      setName('')
      setContent('')
      await reload()
      onChanged()
    }
    setImporting(false)
  }

  async function onPickFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    e.target.value = '' // 允许再次选择同一个文件
    if (!f) return
    const text = await f.text()
    setContent(text)
    setName(extractName(text) || f.name.replace(/\.md$/i, ''))
  }

  async function onToggle(s: SkillInfo) {
    setBusyName(s.name)
    setMsg(null)
    const r = await toggleSkill(s.name, !s.enabled)
    setMsg({ ok: r.ok, text: r.message })
    await reload()
    onChanged()
    setBusyName(null)
  }

  async function onDelete(s: SkillInfo) {
    if (
      !window.confirm(
        `确定永久删除技能「${s.name}」吗？\n\n文件 skills/${s.name}.md 将被移除，此操作不可恢复。`,
      )
    ) {
      return
    }
    setBusyName(s.name)
    setMsg(null)
    const r = await deleteSkill(s.name)
    setMsg({ ok: r.ok, text: r.message })
    await reload()
    onChanged()
    setBusyName(null)
  }

  const enabled = list?.filter((s) => s.enabled) ?? []
  const disabled = list?.filter((s) => !s.enabled) ?? []

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>📚 技能库</h3>
          <button className="btn ghost small" onClick={onClose} title="关闭">
            ✕ 关闭
          </button>
        </div>
        <p className="modal-hint">
          技能 = <code>skills/</code> 目录下的 .md「数据文件」（名字/简介/关键词在
          <code>---</code> 元信息块里）。任何改动<b>下一次提问立即生效</b>（热插拔，无需重启后端）；
          停用只是把文件移进 <code>skills/_disabled/</code>，随时可恢复，命令行同样生效。
        </p>

        {/* ---- 导入区 ---- */}
        <div className="skill-import">
          <div className="import-title">导入新技能</div>
          <textarea
            rows={5}
            placeholder={
              '粘贴 .md 内容（推荐带 --- 元信息块，路由才认识它）：\n---\nname: my-skill\ndescription: 一句话说明干什么\nkeywords: a, b\n---\n技能正文……'
            }
            value={content}
            onChange={(e) => {
              const next = e.target.value
              setContent(next)
              setName((prev) => prev || extractName(next)) // 名字留空时自动识别
            }}
          />
          <div className="import-bar">
            <input
              ref={fileRef}
              type="file"
              accept=".md,text/markdown,.txt"
              style={{ display: 'none' }}
              onChange={(e) => void onPickFile(e)}
            />
            <button
              className="btn ghost small"
              onClick={() => fileRef.current?.click()}
              title="读取本机 .md 文件并填入（技能名自动识别）"
            >
              📂 从 .md 文件读取
            </button>
            <input
              className="name-input"
              placeholder="技能名（留空自动从 name: 识别）"
              value={name}
              spellCheck={false}
              onChange={(e) => setName(e.target.value)}
            />
            <button
              className="btn primary small"
              disabled={importing || !content.trim() || !name}
              onClick={() => void doImport()}
            >
              {importing ? '导入中…' : '导入技能'}
            </button>
          </div>
        </div>

        {msg && (
          <div className={`opmsg ${msg.ok ? 'ok' : 'err'}`}>
            {msg.ok ? '✅' : '❌'} {msg.text}
          </div>
        )}

        {/* ---- 已启用 ---- */}
        <div className="skill-sec">
          <div className="skill-sec-title">
            已启用（{enabled.length}）
            <span className="muted sec-note">会参与自动路由，可被提问命中</span>
          </div>
          {enabled.length === 0 && <div className="empty-list muted">没有启用的技能 —— 停用列表里还有，或导入新的。</div>}
          {enabled.map((s) => (
            <SkillRow
              key={s.name}
              s={s}
              busy={busyName === s.name}
              onToggle={() => void onToggle(s)}
              onDelete={() => void onDelete(s)}
            />
          ))}
        </div>

        {/* ---- 已停用 ---- */}
        <div className="skill-sec">
          <div className="skill-sec-title">
            已停用（{disabled.length}）
            <span className="muted sec-note">文件在 skills/_disabled/，不参与路由</span>
          </div>
          {disabled.length === 0 && <div className="empty-list muted">（无）</div>}
          {disabled.map((s) => (
            <SkillRow
              key={s.name}
              s={s}
              busy={busyName === s.name}
              onToggle={() => void onToggle(s)}
              onDelete={() => void onDelete(s)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function SkillRow({
  s,
  busy,
  onToggle,
  onDelete,
}: {
  s: SkillInfo
  busy: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  return (
    <div className={`skill-row ${s.enabled ? '' : 'dim'}`}>
      <div className="skill-body">
        <div className="skill-name-line">
          <b className="skill-name">{s.name}</b>
          {!s.enabled && <span className="badge off">已停用</span>}
          {!s.description && <span className="badge warn">没有描述，路由很难命中</span>}
          {s.keywords.length > 0 && (
            <span className="kw">
              {s.keywords.map((k) => (
                <code key={k}>{k}</code>
              ))}
            </span>
          )}
        </div>
        {s.description && <div className="skill-desc">{s.description}</div>}
      </div>
      <div className="skill-ops">
        <button className="btn ghost small" disabled={busy} onClick={onToggle}>
          {busy ? '处理中…' : s.enabled ? '停用' : '启用'}
        </button>
        <button className="btn danger small" disabled={busy} onClick={onDelete}>
          删除
        </button>
      </div>
    </div>
  )
}

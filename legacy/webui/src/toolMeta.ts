// 工具的中文展示信息（图标/名称/一句话）—— 与 tools.py 里的 7 个内置工具对应
export interface ToolMeta {
  icon: string
  label: string
  blurb: string
}

export const TOOL_META: Record<string, ToolMeta> = {
  get_time: { icon: '🕐', label: '查询时间', blurb: '真实本地日期时间' },
  calculator: { icon: '🧮', label: '计算器', blurb: '安全解析并计算数学表达式' },
  get_weather: { icon: '🌤️', label: '查天气', blurb: '模拟天气数据（教学示例）' },
  save_memo: { icon: '📝', label: '记备忘', blurb: '写入 memos.json 长期记忆' },
  list_memos: { icon: '📋', label: '回顾备忘', blurb: '列出之前记下的备忘' },
  web_search: { icon: '🌐', label: '联网搜索', blurb: '搜索引擎真实检索（Bing / DuckDuckGo）' },
  fetch_page: { icon: '📄', label: '抓取网页', blurb: '抓取公网网页并转纯文本（防 SSRF）' },
}

const FALLBACK: ToolMeta = { icon: '⚙️', label: '调用工具', blurb: '' }

export function toolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? FALLBACK
}

/** 参数渲染：空参数显示占位，否则压成一行便于扫读 */
export function fmtArgs(args: Record<string, unknown>): string {
  const keys = Object.keys(args ?? {})
  if (keys.length === 0) return '（无参数）'
  const text = JSON.stringify(args, null, 2)
  return text.length > 400 ? JSON.stringify(args) : text
}

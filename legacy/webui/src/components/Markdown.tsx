import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** Markdown 渲染（GFM）。用小智回答 / 技能说明这类模型产出的富文本。 */
function MarkdownImpl({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

export const Markdown = memo(MarkdownImpl)

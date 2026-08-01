import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

export function MarkdownReport({ content }: { content: string }) {
  return (
    <div className="markdown-report">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div className="markdown-table-scroll">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {content || "该报告没有正文。"}
      </ReactMarkdown>
    </div>
  )
}

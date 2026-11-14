import { Workflow } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const markdownComponents = {
    table: ({ children }) => <div className="markdown-table"><table>{children}</table></div>,
};

export default function AgentMessage({ message, children, sources, documentAnswer }) {
    return (
        <div className="agent-message">
            <span className="agent-avatar" aria-hidden="true"><Workflow size={17} /></span>
            <div className="agent-content">
                <h3>AgentFlow AI</h3>
                {children}
                {message && (
                    <div className="markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents} skipHtml
                            disallowedElements={documentAnswer ? ["a", "img"] : undefined} unwrapDisallowed>
                            {message}
                        </ReactMarkdown>
                    </div>
                )}
                {sources?.length > 0 && (
                    <details className="document-citations">
                        <summary>Document sources ({sources.length})</summary>
                        <ul>
                            {sources.map((source) => (
                                <li key={source.id}>
                                    <strong>[{source.id}] {source.filename} · {source.page ? `Page ${source.page}` : `Chunk ${source.chunk_index}`}</strong>
                                    <blockquote>{source.excerpt}</blockquote>
                                </li>
                            ))}
                        </ul>
                    </details>
                )}
            </div>
        </div>
    );
}

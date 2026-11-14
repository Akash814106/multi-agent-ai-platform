import { Workflow } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const markdownComponents = {
    table: ({ children }) => <div className="markdown-table"><table>{children}</table></div>,
};

export default function AgentMessage({ message, children }) {
    return (
        <div className="agent-message">
            <span className="agent-avatar" aria-hidden="true"><Workflow size={17} /></span>
            <div className="agent-content">
                <h3>AgentFlow AI</h3>
                {children}
                {message && (
                    <div className="markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents} skipHtml>
                            {message}
                        </ReactMarkdown>
                    </div>
                )}
            </div>
        </div>
    );
}

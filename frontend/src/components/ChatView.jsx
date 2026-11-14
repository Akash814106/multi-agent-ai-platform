import { useLayoutEffect, useRef, useState } from "react";
import { Activity, ArrowDown, ArrowUpRight, BookOpen, Check, ChevronDown, CircleAlert, Clock, Code, Compass, LoaderCircle, RotateCcw, Workflow } from "lucide-react";
import AgentMessage from "./AgentMessage";
import UserMessage from "./UserMessage";

const prompts = [
    { icon: BookOpen, label: "Explain a concept", text: "Explain how APIs work with a simple example." },
    { icon: Code, label: "Explore an idea", text: "Compare SQL and NoSQL for a small application." },
    { icon: Compass, label: "Make a plan", text: "Help me plan a weekend project to learn React." },
];

const statuses = {
    waiting: { icon: Clock, label: "Waiting" },
    running: { icon: LoaderCircle, label: "Running" },
    completed: { icon: Check, label: "Completed" },
    failed: { icon: CircleAlert, label: "Failed" },
};

function AgentActivity({ activity }) {
    return (
        <details className="agent-activity">
            <summary>
                <Activity size={15} />
                <span className="show-activity">Show Agent Activity</span>
                <span className="hide-activity">Hide Agent Activity</span>
                <ChevronDown size={15} className="activity-chevron" />
            </summary>
            <ul>
                {activity.map((item) => {
                    const status = statuses[item.status];
                    if (!status) return null;
                    const Icon = status.icon;
                    return (
                        <li key={item.id}>
                            <Icon size={16} className={`activity-icon ${item.status}`} />
                            <div>
                                <strong>{item.name}</strong>
                                <p>{item.description}</p>
                                {item.reason && <p className="activity-state failed">{item.reason}</p>}
                            </div>
                            <span className={`activity-state ${item.status}`}>
                                {status.label}
                                {Number.isFinite(item.duration_ms) && <small>{(item.duration_ms / 1000).toFixed(1)}s</small>}
                            </span>
                        </li>
                    );
                })}
            </ul>
        </details>
    );
}

function Welcome({ selectPrompt }) {
    return (
        <div className="chat-welcome">
            <div className="welcome-mark" aria-hidden="true"><Workflow size={34} strokeWidth={1.6} /></div>
            <h2>What can we work on?</h2>
            <p>Ask a question, explore an idea, or make a plan.</p>
            <div className="prompt-grid">
                {prompts.map(({ icon: Icon, label, text }) => (
                    <button type="button" key={label} onClick={() => selectPrompt(text)}>
                        <span className="prompt-icon"><Icon size={17} /></span>
                        <ArrowUpRight size={15} className="prompt-arrow" />
                        <strong>{label}</strong>
                        <span>{text}</span>
                    </button>
                ))}
            </div>
        </div>
    );
}

export default function ChatView({ messages, live, busy, retry, selectPrompt }) {
    const scroller = useRef(null);
    const content = useRef(null);
    const pinned = useRef(true);
    const [showJump, setShowJump] = useState(false);

    useLayoutEffect(() => {
        const viewport = scroller.current;
        function follow() {
            if (pinned.current) viewport.scrollTop = viewport.scrollHeight;
        }
        follow();
        const observer = new ResizeObserver(follow);
        observer.observe(content.current);
        observer.observe(viewport);
        return () => observer.disconnect();
    }, []);

    return (
        <div className="chat-region">
            <div className="chat-scroll" ref={scroller} onScroll={(event) => {
                const viewport = event.currentTarget;
                pinned.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 48;
                setShowJump(!pinned.current);
            }}>
                <div className={messages.length ? "chat-thread" : "chat-empty"} ref={content}>
                    {messages.length === 0 && <Welcome selectPrompt={selectPrompt} />}
                    {messages.map((message, index) => {
                        const current = live?.messageId === message.id ? live : null;
                        const activity = current?.activity ?? message.activity;
                        const answer = current?.text ?? message.answer;
                        return (
                            <section className="chat-turn" key={message.id}>
                                <UserMessage query={message.query} />
                                {(message.status === "pending" || answer || activity?.length > 0 || message.error) && (
                                    <AgentMessage message={answer}>
                                        {activity?.length > 0 && <AgentActivity activity={activity} />}
                                        {message.partial && (
                                            <p className="chat-loading" role="status">
                                                <CircleAlert size={16} /> Partial answer. Some research or review could not be completed.
                                            </p>
                                        )}
                                        {message.status === "pending" && (
                                            <div className="chat-loading" role="status">
                                                <LoaderCircle size={16} />
                                                {answer ? "Finishing response…" : "Working on your request…"}
                                            </div>
                                        )}
                                        {message.error && (
                                            <div className="chat-error">
                                                <p role="alert"><CircleAlert size={17} /> {message.error}</p>
                                                {index === messages.length - 1 && (
                                                    <button type="button" disabled={busy} onClick={() => retry(message)}>
                                                        <RotateCcw size={14} /> Retry
                                                    </button>
                                                )}
                                            </div>
                                        )}
                                    </AgentMessage>
                                )}
                            </section>
                        );
                    })}
                </div>
            </div>
            {showJump && (
                <button className="jump-button" type="button" aria-label="Jump to latest message" onClick={() => {
                    pinned.current = true;
                    scroller.current.scrollTop = scroller.current.scrollHeight;
                    setShowJump(false);
                }}>
                    <ArrowDown size={18} />
                </button>
            )}
        </div>
    );
}

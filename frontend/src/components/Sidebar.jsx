import { useState } from "react";
import { Activity, LogOut, MessageSquare, Pencil, Plus, Trash2, Workflow, X } from "lucide-react";

export default function Sidebar({
    user, conversations, activeId, selectConversation, renameConversation, deleteConversation,
    mobileOpen, close, logout,
}) {
    const [editingId, setEditingId] = useState(null);
    const [title, setTitle] = useState("");

    function saveTitle(id) {
        renameConversation(id, title);
        setEditingId(null);
    }

    return (
        <aside id="workspace-sidebar" className={`workspace-sidebar${mobileOpen ? " is-open" : ""}`}>
            <div className="sidebar-top">
                <div className="wordmark">
                    <span className="brand-mark"><Workflow size={20} /></span>
                    <span>AgentFlow <b>AI</b></span>
                </div>
                <button className="icon-button mobile-close" type="button" aria-label="Close navigation" onClick={close}>
                    <X size={21} />
                </button>
            </div>

            <button className="new-chat" type="button" onClick={() => selectConversation(null)}>
                <Plus size={19} /> New Chat
            </button>

            <nav className="workspace-nav" aria-label="Workspace">
                <button className="nav-item current" type="button" aria-current="page" onClick={close}>
                    <MessageSquare size={16} /> Chat Workspace
                </button>
                <button className="nav-item" type="button" disabled>
                    <Activity size={16} /> Observability <span className="coming-soon">Soon</span>
                </button>
            </nav>

            <section className="history" aria-label="Chat history">
                <h2>Recent chats</h2>
                {conversations.length === 0 && <p className="history-empty">Your chats will appear here.</p>}
                <ul>
                    {conversations.map((conversation) => (
                        <li key={conversation.id} className={conversation.id === activeId ? "selected" : ""}>
                            {editingId === conversation.id ? (
                                <form className="rename-form" onSubmit={(event) => {
                                    event.preventDefault();
                                    saveTitle(conversation.id);
                                }}>
                                    <input autoFocus aria-label="Chat title" value={title}
                                        onChange={(event) => setTitle(event.target.value)}
                                        onBlur={() => saveTitle(conversation.id)}
                                        onKeyDown={(event) => {
                                            if (event.key === "Escape") {
                                                event.stopPropagation();
                                                setEditingId(null);
                                            }
                                        }} />
                                </form>
                            ) : (
                                <>
                                    <button type="button" className="history-title" title={conversation.title}
                                        aria-current={conversation.id === activeId ? "true" : undefined}
                                        onClick={() => selectConversation(conversation.id)}>
                                        {conversation.title}
                                    </button>
                                    <button className="icon-button history-action" type="button" aria-label={`Rename ${conversation.title}`}
                                        onClick={() => {
                                            setTitle(conversation.title);
                                            setEditingId(conversation.id);
                                        }}>
                                        <Pencil size={14} />
                                    </button>
                                    <button className="icon-button history-action delete-chat" type="button" aria-label={`Delete ${conversation.title}`}
                                        onClick={() => deleteConversation(conversation.id)}>
                                        <Trash2 size={14} />
                                    </button>
                                </>
                            )}
                        </li>
                    ))}
                </ul>
            </section>

            <div className="profile">
                <span className="profile-avatar" aria-hidden="true">{user.charAt(0).toUpperCase() || "A"}</span>
                <span className="profile-name" title={user}>{user}</span>
                <button className="icon-button" type="button" aria-label="Sign out" title="Sign out" onClick={logout}>
                    <LogOut size={18} />
                </button>
            </div>
        </aside>
    );
}

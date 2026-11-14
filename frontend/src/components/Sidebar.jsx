import { useLayoutEffect, useRef, useState } from "react";
import { Activity, History, LogOut, MessageSquare, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Trash2, Workflow, X } from "lucide-react";

export default function Sidebar({
    user, conversations, activeId, selectConversation, renameConversation, deleteConversation,
    mobileOpen, collapsed, toggleCollapsed, close, logout,
}) {
    const [editingId, setEditingId] = useState(null);
    const [title, setTitle] = useState("");
    const closeButton = useRef(null);
    const recentHeading = useRef(null);
    const focusRecent = useRef(false);
    const toggleLabel = collapsed ? "Expand sidebar" : "Collapse sidebar";

    useLayoutEffect(() => {
        if (!mobileOpen) return;
        const frame = requestAnimationFrame(() => closeButton.current?.focus());
        return () => cancelAnimationFrame(frame);
    }, [mobileOpen]);

    useLayoutEffect(() => {
        if (!collapsed && focusRecent.current) {
            focusRecent.current = false;
            recentHeading.current?.focus();
        }
    }, [collapsed]);

    function handleDrawerKeys(event) {
        if (!mobileOpen) return;
        if (event.key === "Escape") {
            event.preventDefault();
            close();
        }
        if (event.key !== "Tab") return;

        const controls = [...event.currentTarget.querySelectorAll("button:not(:disabled), input:not(:disabled)")]
            .filter((control) => control.getClientRects().length);
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
        }
    }

    function saveTitle(id) {
        renameConversation(id, title);
        setEditingId(null);
    }

    return (
        <aside id="workspace-sidebar"
            className={`workspace-sidebar${mobileOpen ? " is-open" : ""}${collapsed ? " is-collapsed" : ""}`}
            role={mobileOpen ? "dialog" : undefined} aria-modal={mobileOpen || undefined}
            aria-label="Workspace navigation" onKeyDown={handleDrawerKeys}
            onTransitionEnd={(event) => {
                if (mobileOpen && event.target === event.currentTarget && event.propertyName === "transform"
                    && !event.currentTarget.contains(document.activeElement)) {
                    closeButton.current?.focus();
                }
            }}>
            <div className="sidebar-top">
                <div className="wordmark">
                    <span className="brand-mark"><Workflow size={20} /></span>
                    <span>AgentFlow <b>AI</b></span>
                </div>
                <button className="icon-button sidebar-toggle" type="button" aria-label={toggleLabel}
                    aria-expanded={!collapsed} aria-controls="sidebar-history"
                    data-tooltip={toggleLabel} onClick={toggleCollapsed}>
                    {collapsed ? <PanelLeftOpen size={19} /> : <PanelLeftClose size={19} />}
                </button>
                <button className="icon-button mobile-close" ref={closeButton} type="button" aria-label="Close navigation" onClick={close}>
                    <X size={21} />
                </button>
            </div>

            <button className="new-chat" type="button" aria-label="New Chat" data-tooltip="New Chat"
                onClick={() => selectConversation(null)}>
                <Plus size={19} /> <span className="sidebar-label">New Chat</span>
            </button>

            <nav className="workspace-nav" aria-label="Workspace">
                <button className="nav-item current" type="button" aria-current="page" aria-label="Chat Workspace"
                    data-tooltip="Chat Workspace" onClick={close}>
                    <MessageSquare size={16} /> <span className="sidebar-label">Chat Workspace</span>
                </button>
                <button className="nav-item sidebar-observability" type="button" disabled>
                    <Activity size={16} /> Observability <span className="coming-soon">Soon</span>
                </button>
                <button className="nav-item rail-history" type="button" aria-label="Recent chats" data-tooltip="Recent chats"
                    onClick={() => {
                        focusRecent.current = true;
                        toggleCollapsed();
                    }}>
                    <History size={16} />
                </button>
            </nav>

            <section className="history" id="sidebar-history" aria-label="Chat history">
                <h2 ref={recentHeading} tabIndex={-1}>Recent chats</h2>
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
                <button className="icon-button" type="button" aria-label="Sign out" data-tooltip="Sign out" onClick={logout}>
                    <LogOut size={18} />
                </button>
            </div>
        </aside>
    );
}

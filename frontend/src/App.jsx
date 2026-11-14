import { useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, Menu, Square } from "lucide-react";

import Login from "./pages/Login";
import Register from "./pages/Register";
import useAuth from "./hooks/useAuth";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import Documents from "./components/Documents";
import useConversation from "./hooks/useConversation";
import "./App.css";

function accountFromToken(token) {
    try {
        const encoded = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
        const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
        return JSON.parse(new TextDecoder().decode(bytes)).sub;
    } catch {
        return null;
    }
}

function Workspace({ user, account, token, logout }) {
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
    const [selectedDocuments, setSelectedDocuments] = useState([]);
    const menu = useRef(null);
    const restoreMenuFocus = useRef(false);
    const composer = useRef(null);
    const chat = useConversation(user, account, token);
    const conversation = chat.conversations.find(({ id }) => id === chat.activeId);
    const activeRun = chat.live?.conversationId === chat.activeId ? chat.live : null;

    useLayoutEffect(() => {
        if (!sidebarOpen && restoreMenuFocus.current) {
            restoreMenuFocus.current = false;
            menu.current?.focus();
        }
        if (!sidebarOpen) return;

        const mobile = window.matchMedia("(max-width: 760px)");
        function closeOnDesktop(event) {
            if (!event.matches) setSidebarOpen(false);
        }
        mobile.addEventListener("change", closeOnDesktop);
        return () => mobile.removeEventListener("change", closeOnDesktop);
    }, [sidebarOpen]);

    function closeSidebar() {
        setSidebarOpen(false);
    }

    function selectConversation(id) {
        chat.selectConversation(id);
        setSelectedDocuments([]);
        closeSidebar();
    }

    return (
        <div className="workspace">
            {sidebarOpen && <div className="sidebar-backdrop" aria-hidden="true"
                onClick={closeSidebar} />}
            <Sidebar
                user={user}
                conversations={chat.conversations}
                activeId={chat.activeId}
                selectConversation={selectConversation}
                renameConversation={chat.renameConversation}
                deleteConversation={chat.deleteConversation}
                mobileOpen={sidebarOpen}
                collapsed={sidebarCollapsed}
                toggleCollapsed={() => setSidebarCollapsed((value) => !value)}
                close={closeSidebar}
                logout={logout}
            />

            <main className="workspace-main" inert={sidebarOpen}>
                <header className="workspace-header">
                    <button className="icon-button mobile-menu" ref={menu} type="button" aria-label="Open navigation"
                        aria-expanded={sidebarOpen} aria-controls="workspace-sidebar" aria-haspopup="dialog" onClick={() => {
                            restoreMenuFocus.current = true;
                            setSidebarOpen(true);
                        }}>
                        <Menu size={22} />
                    </button>
                    <h1>Chat Workspace</h1>
                </header>

                <ChatView
                    key={chat.activeId ?? "new"}
                    messages={conversation?.messages ?? []}
                    live={activeRun}
                    busy={Boolean(chat.live)}
                    retry={chat.retry}
                    selectPrompt={(prompt) => {
                        chat.setQuery(prompt);
                        composer.current.focus();
                    }}
                />

                <footer className="composer-region">
                    {chat.storageError && <p className="storage-error" role="status">Chat history could not be saved on this device.</p>}
                    <form className="composer" onSubmit={(event) => {
                        event.preventDefault();
                        chat.sendQuery(selectedDocuments);
                    }}>
                        <Documents token={token} selected={selectedDocuments} setSelected={setSelectedDocuments} />
                        <textarea
                            ref={composer}
                            rows={1}
                            aria-label="Message AgentFlow AI"
                            placeholder="Message AgentFlow AI"
                            value={chat.query}
                            onChange={(event) => chat.setQuery(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                                    event.preventDefault();
                                    if (!event.repeat) chat.sendQuery(selectedDocuments);
                                }
                            }}
                        />
                        {chat.live ? (
                            <button className="send-button" type="button" aria-label="Stop response" title="Stop response"
                                onClick={chat.cancel}>
                                <Square size={16} fill="currentColor" />
                            </button>
                        ) : (
                            <button className="send-button" type="submit" aria-label="Send message" title="Send message"
                                disabled={!chat.query.trim()}>
                                <ArrowUp size={20} />
                            </button>
                        )}
                    </form>
                    <p className="composer-hint">
                        {chat.live && !activeRun ? "A response is running in another chat." : "Enter to send · Shift+Enter for a new line"}
                    </p>
                </footer>
            </main>
        </div>
    );
}

export default function App() {
    const [showRegister, setShowRegister] = useState(false);
    const { user, token, login, register, logout } = useAuth();
    const account = accountFromToken(token);

    if (account) {
        return <Workspace key={account} user={user} account={account} token={token} logout={logout} />;
    }
    if (showRegister) {
        return (
            <Register onRegister={async (...fields) => {
                await register(...fields);
                setShowRegister(false);
            }} goToLogin={() => setShowRegister(false)} />
        );
    }
    return <Login onLogin={login} goToRegister={() => setShowRegister(true)} />;
}

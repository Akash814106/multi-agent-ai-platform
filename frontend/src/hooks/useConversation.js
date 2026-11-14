import { useEffect, useRef, useState } from "react";
import { streamChat } from "../api/chatStream";

function readStorage(key, fallback) {
    try {
        return JSON.parse(localStorage.getItem(key)) ?? fallback;
    } catch {
        return fallback;
    }
}

function initialHistory(historyKey, user, account) {
    const saved = readStorage(historyKey, null);
    if (saved && Array.isArray(saved.conversations)) {
        return {
            ...saved,
            conversations: saved.conversations.map((conversation) => ({
                ...conversation,
                messages: conversation.messages.map((message) => message.status === "pending"
                    ? { ...message, status: "error", error: "The response was interrupted. Please retry." }
                    : message),
            })),
        };
    }

    // The previous workspace stored conversations by username. Match the account
    // recorded by the authenticated chat endpoint before importing a message.
    const legacy = readStorage(`conversations_${user}`, []);
    const conversations = Array.isArray(legacy) ? legacy.flatMap((conversation) => {
        if (!Array.isArray(conversation?.messages)) return [];
        const messages = conversation.messages
            .filter((message) => message?.result?.user === account)
            .map((message) => ({
                id: message.id,
                query: message.query,
                answer: message.result.response?.final_summary,
                status: "complete",
            }));
        if (!messages.length) return [];
        return [{
            id: conversation.id,
            title: messages.length === conversation.messages.length ? conversation.title : messages[0].query,
            messages,
        }];
    }).reverse() : [];
    const activeId = readStorage(`activeConversation_${user}`, null);
    return { conversations, activeId: conversations.some(({ id }) => id === activeId) ? activeId : null };
}

export default function useConversation(user, account, token) {
    const historyKey = `agentflow.history:${account}`;
    const [history, setHistory] = useState(() => initialHistory(historyKey, user, account));
    const [query, setQuery] = useState("");
    const [live, setLive] = useState(null);
    const [storageError, setStorageError] = useState(false);
    const request = useRef(null);
    const { conversations, activeId } = history;

    useEffect(() => {
        let failed = false;
        try {
            localStorage.setItem(historyKey, JSON.stringify(history));
        } catch {
            failed = true;
        }
        queueMicrotask(() => setStorageError(failed));
    }, [history, historyKey]);

    useEffect(() => () => request.current?.abort(), []);

    function selectConversation(id) {
        setHistory((previous) => ({ ...previous, activeId: id }));
        setQuery("");
    }

    function updateConversation(id, update) {
        setHistory((previous) => ({
            ...previous,
            conversations: previous.conversations.map((conversation) =>
                conversation.id === id ? update(conversation) : conversation),
        }));
    }

    function renameConversation(id, title) {
        const trimmed = title.trim();
        if (trimmed) updateConversation(id, (conversation) => ({ ...conversation, title: trimmed }));
    }

    function deleteConversation(id) {
        if (live?.conversationId === id) request.current?.abort();
        setHistory((previous) => ({
            activeId: previous.activeId === id ? null : previous.activeId,
            conversations: previous.conversations.filter((conversation) => conversation.id !== id),
        }));
        if (activeId === id) setQuery("");
    }

    function sendQuery(documentIds = []) {
        const text = query.trim();
        if (!text || request.current) return;
        const conversationId = activeId ?? crypto.randomUUID();
        const message = { id: crypto.randomUUID(), query: text, status: "pending", documentIds };
        setHistory((previous) => ({
            activeId: conversationId,
            conversations: previous.activeId === null
                ? [{ id: conversationId, title: text.replace(/\s+/g, " ").slice(0, 72), messages: [message] }, ...previous.conversations]
                : previous.conversations.map((conversation) => conversation.id === conversationId
                    ? { ...conversation, messages: [...conversation.messages, message] }
                    : conversation),
        }));
        setQuery("");
        void run(conversationId, message);
    }

    function retry(message) {
        if (activeId === null || request.current) return;
        const pending = { id: message.id, query: message.query, status: "pending", documentIds: message.documentIds };
        updateMessage(activeId, message.id, pending);
        void run(activeId, pending);
    }

    function updateMessage(conversationId, messageId, replacement) {
        updateConversation(conversationId, (conversation) => ({
            ...conversation,
            messages: conversation.messages.map((message) => message.id === messageId ? replacement : message),
        }));
    }

    async function run(conversationId, message) {
        const controller = new AbortController();
        request.current = controller;
        let activity = [];
        setLive({ conversationId, messageId: message.id, text: "", activity });

        try {
            const { answer, partial, sources } = await streamChat(message.query, token, controller.signal, (name, event) => {
                if (name === "activity") {
                    activity = activity.some((item) => item.id === event.id)
                        ? activity.map((item) => item.id === event.id ? event : item)
                        : [...activity, event];
                    const snapshot = activity;
                    setLive((previous) => ({ ...previous, activity: snapshot }));
                } else if (name === "delta") {
                    setLive((previous) => ({ ...previous, text: previous.text + event.text }));
                }
            }, message.documentIds);
            updateMessage(conversationId, message.id, { ...message, status: "complete", answer, activity, partial, sources });
        } catch (error) {
            updateMessage(conversationId, message.id, {
                ...message,
                status: "error",
                error: controller.signal.aborted ? "Response stopped." : error.message,
                activity: activity.filter((item) => item.status === "completed" || item.status === "failed"),
            });
        } finally {
            request.current = null;
            setLive(null);
        }
    }

    return {
        conversations, activeId, query, setQuery, live, storageError,
        selectConversation, renameConversation, deleteConversation, sendQuery, retry,
        cancel: () => request.current?.abort(),
    };
}

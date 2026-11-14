import { readEvents } from "./events";

const CHAT_TIMEOUT_MS = 5 * 60 * 1000;

export async function streamChat(query, token, signal, onEvent, documentIds = []) {
    const timeout = AbortSignal.timeout(CHAT_TIMEOUT_MS);
    let response;
    try {
        response = await fetch(`${import.meta.env.VITE_API_URL?.replace(/\/+$/, "") ?? ""}/chat/stream`, {
            method: "POST",
            headers: {
                Accept: "text/event-stream",
                Authorization: `Bearer ${token}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ query, document_ids: documentIds }),
            signal: AbortSignal.any([signal, timeout]),
        });

        if (response.status === 401 || response.status === 403) {
            throw new Error("Your session has expired. Sign out and sign in again.");
        }
        if (response.status === 422) {
            throw new Error("Check the document selection and use a question of at most 2,000 characters.");
        }
        if (!response.ok || !response.headers.get("content-type")?.includes("text/event-stream")) {
            throw new Error("The chat service is unavailable. Please retry.");
        }
        const result = await readEvents(response.body, onEvent);
        return {
            answer: result.answer,
            partial: result.completion_status === "partial",
            sources: result.sources,
        };
    } catch (error) {
        if (timeout.aborted && !signal.aborted) {
            throw new Error("The response took too long. Please retry.", { cause: error });
        }
        if (error instanceof TypeError) {
            throw new Error("The connection to the chat service was interrupted. Please retry.", { cause: error });
        }
        throw error;
    }
}

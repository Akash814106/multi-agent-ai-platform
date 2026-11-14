const CHAT_TIMEOUT_MS = 5 * 60 * 1000;

export async function streamChat(query, token, signal, onEvent) {
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
            body: JSON.stringify({ query }),
            signal: AbortSignal.any([signal, timeout]),
        });

        if (response.status === 401 || response.status === 403) {
            throw new Error("Your session has expired. Sign out and sign in again.");
        }
        if (!response.ok || !response.headers.get("content-type")?.includes("text/event-stream")) {
            throw new Error("The chat service is unavailable. Please retry.");
        }

        return await readChatEvents(response.body, onEvent);
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

async function readChatEvents(body, onEvent) {
    const reader = body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";

    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += value;
            let separator;
            while ((separator = /\r?\n\r?\n/.exec(buffer))) {
                const frame = buffer.slice(0, separator.index);
                buffer = buffer.slice(separator.index + separator[0].length);
                const lines = frame.split(/\r?\n/);
                const name = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
                const data = lines.filter((line) => line.startsWith("data:"))
                    .map((line) => line.slice(5).trimStart()).join("\n");

                if (!["activity", "delta", "done", "error"].includes(name) || !data) continue;
                const payload = JSON.parse(data);
                if (name === "done") return {
                    answer: payload.answer,
                    partial: payload.completion_status === "partial",
                };
                if (name === "error") throw new Error(payload.message);
                onEvent(name, payload);
            }
        }
        throw new Error("The response ended before it finished. Please retry.");
    } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}

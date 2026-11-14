export async function readEvents(body, onEvent) {
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
                if (!name || !data) continue;
                const payload = JSON.parse(data);
                if (name === "done") return payload;
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

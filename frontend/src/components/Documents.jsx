import { useEffect, useRef, useState } from "react";
import { Paperclip, Trash2 } from "lucide-react";
import api from "../api/api";
import { readEvents } from "../api/events";

const statusNames = {
    processing: "Processing",
    indexed: "Indexed",
    failed: "Failed",
    deleting: "Deleting",
    delete_failed: "Deletion failed",
};

function fileSize(bytes) {
    return bytes < 1024 ? bytes + " B" : (bytes / 1024).toFixed(1) + " KB";
}

function errorText(error) {
    const detail = error.response?.data?.detail;
    return typeof detail === "string" ? detail : error.message;
}

export default function Documents({ token, selected, setSelected }) {
    const [documents, setDocuments] = useState([]);
    const [open, setOpen] = useState(false);
    const [error, setError] = useState("");
    const [deleting, setDeleting] = useState(null);
    const [upload, setUpload] = useState(null);
    const input = useRef(null);
    const uploadController = useRef(null);
    const headers = { Authorization: "Bearer " + token };

    function refresh(signal) {
        return api.get("/documents", { headers, signal }).then(({ data }) => setDocuments(data));
    }

    useEffect(() => {
        const controller = new AbortController();
        if (open) {
            api.get("/documents", { headers: { Authorization: "Bearer " + token }, signal: controller.signal })
                .then(({ data }) => setDocuments(data))
                .catch((failure) => {
                    if (!controller.signal.aborted) setError(errorText(failure));
                });
        }
        return () => controller.abort();
    }, [open, token]);

    useEffect(() => () => uploadController.current?.abort(), []);

    async function uploadFile(file) {
        if (!file || uploadController.current) return;
        const controller = new AbortController();
        uploadController.current = controller;
        setOpen(true);
        setError("");
        setUpload({ filename: file.name, size: file.size, status: "Uploading" });
        try {
            const response = await fetch(api.getUri({ url: "/documents" }), {
                method: "POST",
                headers: { ...headers, "X-Document-Filename": encodeURIComponent(file.name) },
                body: file,
                signal: AbortSignal.any([controller.signal, AbortSignal.timeout(240000)]),
            });
            if (!response.ok || !response.headers.get("content-type")?.includes("text/event-stream")) {
                const body = await response.json().catch(() => ({}));
                throw new Error(typeof body.detail === "string" ? body.detail : "The upload could not start.");
            }
            const document = await readEvents(response.body, (event) => {
                if (event === "processing") setUpload({ filename: file.name, size: file.size, status: "Processing" });
            });
            setDocuments((current) => [document, ...current.filter((row) => row.id !== document.id)]);
            setUpload(null);
        } catch (failure) {
            if (!controller.signal.aborted) {
                setUpload({ filename: file.name, size: file.size, status: "Failed" });
                setError(errorText(failure));
                await refresh(controller.signal).catch(() => {});
            }
        } finally {
            uploadController.current = null;
        }
    }

    async function remove(documentId) {
        setDeleting(documentId);
        setError("");
        try {
            await api.delete("/documents/" + documentId, { headers });
            setDocuments((rows) => rows.filter((row) => row.id !== documentId));
            setSelected((ids) => ids.filter((id) => id !== documentId));
        } catch (failure) {
            setError(errorText(failure));
            await refresh().catch(() => {});
        } finally {
            setDeleting(null);
        }
    }

    return (
        <div className="document-controls">
            <button className="icon-button document-trigger" type="button" aria-label="Manage documents"
                aria-controls="document-picker" aria-expanded={open} title="Manage documents"
                onClick={() => setOpen((value) => !value)}>
                <Paperclip size={19} />
                {selected.length > 0 && <span>{selected.length}</span>}
            </button>
            {open && (
                <section className="document-picker" id="document-picker" aria-label="Documents">
                    <div className="document-picker-header">
                        <h2>Documents</h2>
                        <button type="button" className="document-upload" disabled={["Uploading", "Processing"].includes(upload?.status)}
                            onClick={() => input.current.click()}>Upload PDF or TXT</button>
                        <input type="file" hidden accept=".pdf,.txt" ref={input} onChange={(event) => {
                            const file = event.target.files[0];
                            event.target.value = "";
                            void uploadFile(file);
                        }} />
                    </div>
                    {upload && (
                        <p role="status" className="document-upload-status">
                            {upload.filename} · {fileSize(upload.size)} · {upload.status}
                        </p>
                    )}
                    {error && <p role="alert" className="document-error">{error}</p>}
                    {documents.length === 0 && !upload && <p className="document-upload-status">Upload a document to ask questions about it.</p>}
                    <ul>
                        {documents.map((row) => (
                            <li key={row.id}>
                                <label>
                                    <input type="checkbox" checked={selected.includes(row.id)}
                                        disabled={row.status !== "indexed" || deleting === row.id ||
                                            (!selected.includes(row.id) && selected.length >= 5)}
                                        onChange={(event) => {
                                            const checked = event.target.checked;
                                            setSelected((ids) => checked ? [...ids, row.id] : ids.filter((id) => id !== row.id));
                                        }} />
                                    <span>
                                        <strong>{row.filename}</strong>
                                        <small>{fileSize(row.size)} · {statusNames[row.status]}</small>
                                        {row.error && <small className="document-error">{row.error}</small>}
                                    </span>
                                </label>
                                <button type="button" className="icon-button" aria-label={"Delete " + row.filename}
                                    disabled={deleting !== null || ["processing", "deleting"].includes(row.status)}
                                    onClick={() => remove(row.id)}><Trash2 size={16} /></button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}

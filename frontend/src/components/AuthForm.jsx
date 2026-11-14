import { useRef, useState } from "react";
import { Workflow } from "lucide-react";

export default function AuthForm({ title, submit, switchLabel, switchMode, children }) {
    const [pending, setPending] = useState(false);
    const [error, setError] = useState("");
    const submitting = useRef(false);

    async function handleSubmit(event) {
        event.preventDefault();
        if (submitting.current) return;
        submitting.current = true;
        setPending(true);
        setError("");
        try {
            await submit();
        } catch (failure) {
            const status = failure.response?.status;
            const detail = failure.response?.data?.detail;
            if (status === 503) {
                setError("Account storage is temporarily unavailable. Please try again later.");
            } else {
                setError(typeof detail === "string" && status < 500
                    ? detail
                    : "Could not reach the account service. Please try again.");
            }
        } finally {
            submitting.current = false;
            setPending(false);
        }
    }

    return (
        <main className="auth-screen">
            <section className="auth-card">
                <div className="wordmark">
                    <span className="brand-mark"><Workflow size={20} /></span>
                    <span>AgentFlow <b>AI</b></span>
                </div>
                <h1>{title}</h1>
                <form onSubmit={handleSubmit}>
                    {children}
                    {error && <p className="auth-error" role="alert">{error}</p>}
                    <button className="auth-submit" type="submit" disabled={pending}>
                        {pending ? "Please wait…" : title}
                    </button>
                </form>
                <button className="auth-switch" type="button" disabled={pending} onClick={switchMode}>
                    {switchLabel}
                </button>
            </section>
        </main>
    );
}

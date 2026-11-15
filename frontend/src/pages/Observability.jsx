import { useEffect, useState } from "react";
import { Activity, ChevronDown, ChevronRight, Clock, Database, ListChecks, RefreshCw } from "lucide-react";
import api from "../api/api";
import "./Observability.css";

const workflows = { general_chat: "General chat", document_rag: "Document RAG" };
const categories = {
    rate_limit: "Provider rate limit", timeout: "Provider timeout", network: "Provider connection",
    workflow_timeout: "Workflow timeout", document_storage: "Storage error",
    document_retrieval: "Document retrieval", retrieval_busy: "Retrieval capacity",
    invalid_response: "Invalid response", document_citation_missing: "Missing document citation",
    provider_configuration: "Provider configuration", provider_request: "Provider request",
    provider_unavailable: "Provider unavailable", workflow: "Workflow error", unknown: "Unknown error",
    search_failure: "Web search failure", no_evidence: "No web evidence", validation: "Validation",
};
const statuses = {
    success: "Success", partial: "Partial", failed: "Failed", cancelled: "Cancelled",
    timeout: "Timeout", completed: "Completed", incomplete: "Incomplete",
};

function useMetrics(path, token) {
    const [result, setResult] = useState(null);
    useEffect(() => {
        const controller = new AbortController();
        api.get(path, { headers: { Authorization: "Bearer " + token }, signal: controller.signal })
            .then(({ data }) => setResult({ path, data }))
            .catch((error) => {
                if (!controller.signal.aborted) setResult({
                    path, error: error.response?.status === 401
                        ? "Sign in again to see observability data."
                        : "Could not load this data. Try refreshing.",
                });
            });
        return () => controller.abort();
    }, [path, token]);
    return result?.path === path ? result : null;
}

const duration = (ms) => ms == null ? "—" : ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(2) + " s";
const percent = (value) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const category = (key) => categories[key] ?? "Other error";
const time = (timestamp) => new Date(timestamp).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

function Status({ status }) {
    return <span className={"obs-status obs-" + status}>{statuses[status] ?? status}</span>;
}

function Result({ result, children }) {
    if (!result) return <p className="obs-note" role="status">Loading…</p>;
    if (result.error) return <p role="alert" className="obs-error">{result.error}</p>;
    return children(result.data);
}

function Summary({ period, token }) {
    const result = useMetrics("/observability/summary?period=" + period, token);
    return <Result result={result}>{(data) => {
        const { outcomes, total, incomplete, retrieval, agents } = data;
        const failed = (outcomes.failed ?? 0) + (outcomes.timeout ?? 0);
        const failures = Object.keys({ ...data.failures, ...data.step_failures });
        const stats = [
            ["Total requests", total, Activity], ["Successful", outcomes.success ?? 0, ListChecks],
            ["Failed or timed out", failed, Activity], ["Success rate", percent(data.success_rate), ListChecks],
            ["Average latency", duration(data.average_duration_ms), Clock],
        ];
        return <>
            <section aria-label="Request summary" className="obs-stats">
                {stats.map(([label, value, Icon]) => <div className="obs-card obs-stat" key={label}>
                    <span><Icon size={15} aria-hidden="true" />{label}</span><strong>{value}</strong>
                </div>)}
            </section>
            <p className="obs-note">
                Success rate counts successful requests out of all recorded requests.
                {total > 0 && <> Partial: {outcomes.partial ?? 0} · Cancelled: {outcomes.cancelled ?? 0} · Incomplete: {incomplete}</>}
            </p>
            {total === 0 ? <p className="obs-card obs-empty">No requests in this time range. Send a chat message, then refresh.</p> : <>
                <div className="obs-pair">
                    <section className="obs-card">
                        <h2>Workflows</h2>
                        {Object.entries(workflows).map(([key, label]) => {
                            const count = data.workflows[key] ?? 0;
                            return <div className="obs-workflow" key={key}>
                                <div><span>{label}</span><strong>{count}</strong></div>
                                <div className="obs-track" aria-hidden="true"><span style={{ width: percent(count / total) }} /></div>
                            </div>;
                        })}
                        <p className="obs-note">{retrieval?.measured && Number.isFinite(retrieval.average_passages)
                            ? retrieval.measured + " RAG retrievals measured · " + retrieval.average_passages.toFixed(1) + " average passages · " + retrieval.empty + " empty"
                            : "No measured document retrievals."}</p>
                    </section>
                    <section className="obs-card">
                        <h2>Failure breakdown</h2>
                        {failures.length ? <div className="obs-table-wrap"><table aria-label="Failure breakdown">
                            <thead><tr><th>Category</th><th>Requests</th><th>Steps</th></tr></thead>
                            <tbody>{failures.map((key) => <tr key={key}><td>{category(key)}</td>
                                <td>{data.failures[key] ?? 0}</td><td>{data.step_failures[key] ?? 0}</td></tr>)}</tbody>
                        </table></div> : <p className="obs-note">No failure categories recorded.</p>}
                    </section>
                </div>
                <section className="obs-card">
                    <h2>Agent performance</h2>
                    {agents.length ? <div className="obs-table-wrap"><table aria-label="Agent performance">
                        <thead><tr><th>Agent</th><th>Executions</th><th>Avg. duration</th><th>Failures</th><th>Failure rate</th></tr></thead>
                        <tbody>{agents.map((agent) => <tr key={agent.name}>
                            <td>{agent.name}</td><td>{agent.executions}</td><td>{duration(agent.average_duration_ms)}</td>
                            <td>{agent.failures}</td><td>{percent(agent.measured ? agent.failures / agent.measured : null)}</td>
                        </tr>)}</tbody>
                    </table></div> : <p className="obs-note">No agent executions were captured.</p>}
                </section>
            </>}
        </>;
    }}</Result>;
}

function RequestDetail({ identifier, token }) {
    const result = useMetrics("/observability/requests/" + identifier, token);
    return <Result result={result}>{(data) => <div className="obs-request-detail">
        <p><strong>Request ID</strong> <code>{data.request_id}</code></p>
        <p className="obs-note">Groq calls: {data.llm_calls} · Retrieved passages: {data.retrieved_passages ?? "—"}
            {!data.complete && " · Metrics incomplete"}
            {data.error_category && " · " + category(data.error_category)}</p>
        {data.steps.length ? <ul className="obs-steps" aria-label="Agent executions">
            {data.steps.map((step) => <li key={step.id}>
                <span>{step.name}{step.error_category && <small>{category(step.error_category)}</small>}</span>
                <Status status={step.status} /><span>{duration(step.duration_ms)}</span>
            </li>)}
        </ul> : <p className="obs-note">No steps were recorded for this request.</p>}
    </div>}</Result>;
}

function History({ period, token }) {
    const [page, setPage] = useState(1);
    const [selected, setSelected] = useState(null);
    const result = useMetrics("/observability/requests?period=" + period + "&page=" + page, token);
    const current = result?.data;
    function changePage(value) {
        setPage(value);
        setSelected(null);
    }

    return <section className="obs-card">
        <h2>Recent requests</h2>
        <Result result={result}>{({ items }) => items.length ? <div className="obs-table-wrap">
            <table aria-label="Recent requests"><thead><tr>
                <th>Time</th><th>Request ID</th><th>Workflow</th><th>Duration</th><th>Status</th><th>Agents</th>
            </tr></thead>
                {items.map((item) => {
                    const expanded = selected === item.request_id;
                    return <tbody key={item.request_id}>
                        <tr>
                            <td>{time(item.started_at)}</td>
                            <td><button type="button" className="obs-id" aria-expanded={expanded}
                                aria-controls={"request-" + item.request_id}
                                aria-label={"Request " + item.request_id}
                                onClick={() => setSelected(expanded ? null : item.request_id)}>
                                {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                                <code>{item.request_id.slice(0, 8)}</code>
                            </button></td>
                            <td>{workflows[item.workflow]}</td><td>{duration(item.duration_ms)}</td>
                            <td><Status status={item.status} /></td><td>{item.agent_count}</td>
                        </tr>
                        <tr id={"request-" + item.request_id} hidden={!expanded}>
                            <td colSpan={6}>{expanded && <RequestDetail identifier={item.request_id} token={token} />}</td>
                        </tr>
                    </tbody>;
                })}
            </table>
        </div> : <p className="obs-note">No recorded requests on this page.</p>}</Result>
        {(page > 1 || current?.has_more) && <div className="obs-pagination" aria-label="History pages">
            <button type="button" disabled={page === 1} onClick={() => changePage(page - 1)}>Previous</button>
            <span aria-live="polite">Page {page}</span>
            <button type="button" disabled={!current?.has_more} onClick={() => changePage(page + 1)}>Next</button>
        </div>}
    </section>;
}

function Benchmark({ token }) {
    const result = useMetrics("/observability/benchmark", token);
    return <section className="obs-card">
        <h2><Database size={17} aria-hidden="true" /> Synthetic RAG benchmark</h2>
        <Result result={result}>{({ benchmark }) => benchmark ? <>
            <div className="obs-benchmark">
                <div>Recall@{benchmark.top_k}<strong>{percent(benchmark.recall_at_k)}</strong></div>
                <div>Precision@{benchmark.top_k}<strong>{percent(benchmark.precision_at_k)}</strong></div>
                <div>Hit Rate@{benchmark.top_k}<strong>{percent(benchmark.hit_rate_at_k)}</strong></div>
            </div>
            <p className="obs-note">Dataset {benchmark.dataset_version} · {benchmark.embedding_model} · {benchmark.passed_cases}/{benchmark.total_cases} cases passed
                {benchmark.evaluated_at && " · " + time(benchmark.evaluated_at)}</p>
        </> : <p className="obs-note">No published benchmark results.</p>}</Result>
    </section>;
}

export default function Observability({ token }) {
    const [period, setPeriod] = useState("7d");
    const [refresh, setRefresh] = useState(0);
    return <div className="observability">
        <div className="obs-toolbar">
            <div>
                <h2>Request overview</h2>
                <p className="obs-note">Recorded activity for your account. Recent or missing telemetry may leave totals incomplete.</p>
            </div>
            <div className="obs-filters">
                <label>Time range <select value={period} onChange={(event) => setPeriod(event.target.value)}>
                    <option value="24h">Last 24 hours</option>
                    <option value="7d">Last 7 days</option>
                    <option value="30d">Last 30 days</option>
                </select></label>
                <button type="button" className="icon-button" title="Refresh metrics" aria-label="Refresh metrics"
                    onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={17} /></button>
            </div>
        </div>
        <Summary key={"summary:" + period + ":" + refresh} period={period} token={token} />
        <History key={"history:" + period + ":" + refresh} period={period} token={token} />
        <Benchmark key={"benchmark:" + refresh} token={token} />
    </div>;
}

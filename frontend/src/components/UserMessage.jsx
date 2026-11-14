export default function UserMessage({ query }) {
    return <p className="user-message"><span className="sr-only">You: </span>{query}</p>;
}

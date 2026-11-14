import { useState } from "react";
import AuthForm from "../components/AuthForm";

export default function Register({ onRegister, goToLogin }) {
    const [username, setUsername] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");

    return (
        <AuthForm title="Create account" submit={() => onRegister(username, email, password)}
            switchLabel="Back to sign in" switchMode={goToLogin}>
            <label>
                Username
                <input autoComplete="username" required value={username}
                    onChange={(event) => setUsername(event.target.value)} />
            </label>
            <label>
                Email
                <input type="email" autoComplete="email" required value={email}
                    onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label>
                Password
                <input type="password" autoComplete="new-password" required value={password}
                    onChange={(event) => setPassword(event.target.value)} />
            </label>
        </AuthForm>
    );
}

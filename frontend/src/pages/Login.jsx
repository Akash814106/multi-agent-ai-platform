import { useState } from "react";
import AuthForm from "../components/AuthForm";

export default function Login({ onLogin, goToRegister }) {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");

    return (
        <AuthForm title="Sign in" submit={() => onLogin(email, password)}
            switchLabel="Create an account" switchMode={goToRegister}>
            <label>
                Email
                <input type="email" autoComplete="email" required value={email}
                    onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label>
                Password
                <input type="password" autoComplete="current-password" required value={password}
                    onChange={(event) => setPassword(event.target.value)} />
            </label>
        </AuthForm>
    );
}

from backend.utils.groq_client import create_groq_client


def main():
    llm = create_groq_client(1)
    try:
        answer = "".join(chunk.text for chunk in llm.stream("Say hello in one short sentence."))
    except Exception as error:
        print(f"Groq request failed: {type(error).__name__}, status={getattr(error, 'status_code', None)}, code={getattr(error, 'code', None)}")
        return 1

    if not answer.strip():
        print("Groq returned an empty answer.")
        return 1
    print(f"Received a streamed answer from {llm.model_name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

class ChatError(Exception):
    def __init__(self, status_code: int, detail: str, reason: str | None = None, *, retry_after: int | None = None):
        self.status_code = status_code
        self.detail = detail
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(detail)


class InvalidAgentResponse(ChatError):
    def __init__(self, stage: str = "desconhecido"):
        self.stage = stage
        super().__init__(502, "A IA retornou uma resposta invalida. Tente novamente.")

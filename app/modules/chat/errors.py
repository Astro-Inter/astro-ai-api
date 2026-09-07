class ChatError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class InvalidAgentResponse(ChatError):
    def __init__(self):
        super().__init__(502, "A IA retornou uma resposta invalida. Tente novamente.")

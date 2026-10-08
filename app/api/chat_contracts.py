"""Respostas de erro compartilhadas pelo chat e pelo histórico de sessões."""

from app.modules.chat.schemas import SessionErrorResponse


def session_error(description: str, example: str | None = None):
    return {
        "model": SessionErrorResponse, "description": description,
        "content": {"application/json": {"example": {"detail": example or description}}},
    }


SESSION_ERRORS = {
    401: session_error("Bearer Firebase ausente, inválido, expirado ou revogado.",
                       "Credenciais de autenticacao invalidas."),
    403: session_error("Usuario sem acesso ao Astro."),
    429: session_error("Muitas operacoes simultaneas. Tente novamente."),
    503: session_error("Serviço de autenticação, autorização ou histórico indisponível.",
                       "Historico de conversas indisponivel."),
    504: session_error("Operacao excedeu o tempo de resposta. Tente novamente."),
}

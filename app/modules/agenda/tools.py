from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import config as app_config
from app.core.security import CurrentUser
from app.infrastructure.mcp_google_calendar import get_google_calendar_mcp_client
from app.modules.chat.schemas import SpecialistResult


SituacaoTreinamento = Literal["a_realizar", "concluidos", "todos"]


class ConsultarTreinamentosArgs(BaseModel):
    """Filtros públicos; o usuário consultado vem apenas da autenticação."""

    model_config = ConfigDict(extra="forbid")

    situacao: SituacaoTreinamento = Field(
        default="a_realizar",
        description="a_realizar mostra participações pendentes ou rejeitadas em eventos ativos.",
    )
    pagina: int = Field(default=1, ge=1, le=1000)
    limite: int = Field(default=5, ge=1, le=10)


class ConsultarEventosGoogleArgs(BaseModel):
    """Intervalo autorizado para consultar o calendário do próprio usuário."""

    model_config = ConfigDict(extra="forbid")

    inicio: datetime
    fim: datetime
    limite: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validar_intervalo(self):
        if self.inicio.tzinfo is None or self.fim.tzinfo is None:
            raise ValueError("Datas da agenda exigem fuso horário.")
        if self.fim <= self.inicio:
            raise ValueError("O fim do intervalo deve ser posterior ao início.")
        if (self.fim - self.inicio).days > 366:
            raise ValueError("A consulta não pode ultrapassar 366 dias.")
        return self


class CriarEventoGoogleArgs(BaseModel):
    """Dados da prévia ou confirmação de um evento no Google Calendar."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    titulo: str = Field(min_length=1, max_length=180)
    inicio: datetime
    fim: datetime
    descricao: str | None = Field(default=None, max_length=2000)
    confirmar: bool = False

    @model_validator(mode="after")
    def validar_evento(self):
        if self.inicio.tzinfo is None or self.fim.tzinfo is None:
            raise ValueError("Datas do evento exigem fuso horário.")
        if self.fim <= self.inicio:
            raise ValueError("O término deve ser posterior ao início.")
        if self.fim - self.inicio > timedelta(days=7):
            raise ValueError("A duração do evento não pode ultrapassar sete dias.")
        return self


class AgendaToolDecision(BaseModel):
    """Decisão do agente de Agenda entre consultar treinamentos e responder."""

    model_config = ConfigDict(extra="forbid")

    acao: Literal[
        "consultar_treinamentos",
        "consultar_google_calendar",
        "criar_evento_google_calendar",
        "responder",
    ]
    filtros: (
        ConsultarTreinamentosArgs
        | ConsultarEventosGoogleArgs
        | CriarEventoGoogleArgs
        | None
    ) = None
    resposta: SpecialistResult | None = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_filtros_ausentes(cls, data):
        if not isinstance(data, dict):
            return data
        action = data.get("acao")
        filters = data.get("filtros")
        if action == "consultar_treinamentos" and filters is None:
            filters = {}
        schemas = {
            "consultar_treinamentos": ConsultarTreinamentosArgs,
            "consultar_google_calendar": ConsultarEventosGoogleArgs,
            "criar_evento_google_calendar": CriarEventoGoogleArgs,
        }
        schema = schemas.get(action)
        if schema is not None and not isinstance(filters, schema):
            filters = schema.model_validate(filters)
        if schema is not None:
            return {**data, "filtros": filters}
        return data

    @model_validator(mode="after")
    def validar_acao(self):
        expected = {
            "consultar_treinamentos": ConsultarTreinamentosArgs,
            "consultar_google_calendar": ConsultarEventosGoogleArgs,
            "criar_evento_google_calendar": CriarEventoGoogleArgs,
        }
        filter_type = expected.get(self.acao)
        if filter_type is not None and (
            not isinstance(self.filtros, filter_type) or self.resposta is not None
        ):
            raise ValueError("Consulta exige filtros e nao aceita resposta direta.")
        if self.acao == "responder" and (self.resposta is None or self.filtros is not None):
            raise ValueError("Resposta direta exige resultado e nao aceita filtros.")
        if self.resposta is not None and self.resposta.dominio != "agenda":
            raise ValueError("Resposta deve pertencer ao dominio de agenda.")
        return self


def get_postgres_connection():
    """Abre uma conexão curta e somente leitura com o PostgreSQL."""
    return psycopg.connect(
        app_config.DATABASE_URL,
        autocommit=True,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c default_transaction_read_only=on",
    )


def _usuario_do_contexto(runtime_config: RunnableConfig) -> CurrentUser | None:
    raw_user = (runtime_config or {}).get("configurable", {}).get("usuario_atual")
    try:
        return raw_user if isinstance(raw_user, CurrentUser) else CurrentUser.model_validate(raw_user)
    except Exception:
        return None


def _valor_publico(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _configuravel(runtime_config: RunnableConfig) -> dict:
    return (runtime_config or {}).get("configurable", {})


def _rascunho_evento(
    *, session_id: str, titulo: str, inicio: datetime, fim: datetime,
    descricao: str | None, fuso: str,
) -> dict:
    return {
        "tipo": "criar_evento_google_calendar",
        "session_id": session_id,
        "id_evento": "astro" + uuid4().hex,
        "titulo": titulo,
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "descricao": descricao,
        "fuso": fuso,
        "criado_em": datetime.now(timezone.utc).isoformat(),
    }


def _confirmacao_evento_valida(
    configurable: dict, pending: dict, *, session_id: str,
    titulo: str, inicio: datetime, fim: datetime, descricao: str | None,
) -> bool:
    try:
        created_at = datetime.fromisoformat(pending.get("criado_em", ""))
        active = (
            created_at.tzinfo is not None
            and created_at >= datetime.now(timezone.utc) - timedelta(minutes=30)
        )
    except (TypeError, ValueError):
        active = False
    return (
        active
        and configurable.get("confirmacao_explicita") is True
        and pending.get("tipo") == "criar_evento_google_calendar"
        and pending.get("session_id") == session_id
        and pending.get("titulo") == titulo
        and pending.get("inicio") == inicio.isoformat()
        and pending.get("fim") == fim.isoformat()
        and pending.get("descricao") == descricao
        and isinstance(pending.get("id_evento"), str)
        and isinstance(pending.get("fuso"), str)
    )


@tool("consultar_treinamentos", args_schema=ConsultarTreinamentosArgs)
def consultar_treinamentos(
    situacao: SituacaoTreinamento = "a_realizar",
    pagina: int = 1,
    limite: int = 5,
    config: RunnableConfig = None,
) -> dict:
    """Consulta treinamentos atribuídos ao usuário autenticado.

    `a_realizar` inclui apenas eventos ativos cujas participações estejam
    pendentes, rejeitadas ou sem registro de conclusão. A ferramenta não deduz
    inscrição a partir do cargo ou de NRs obrigatórias.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if user.role == "ADMIN":
        return {
            "status": "nao_aplicavel",
            "mensagem": "Administradores nao possuem participacao funcional em treinamentos.",
        }
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de treinamentos indisponivel."}

    status_filter = {
        "a_realizar": (
            "AND evento.status = 'ATIVO' "
            "AND COALESCE(conclusao.status, 'PENDENTE') IN ('PENDENTE', 'REJEITADO')"
        ),
        "concluidos": "AND conclusao.status = 'CONCLUIDO'",
        "todos": "",
    }[situacao]
    from_clause = f"""
        FROM turma_funcionario AS participacao
        JOIN turma
          ON turma.id_turma = participacao.turma_id
        JOIN evento
          ON evento.id_evento = turma.evento_id
        LEFT JOIN conclusao_evento AS conclusao
          ON conclusao.turma_funcionario_id = participacao.id_turma_funcionario
        LEFT JOIN nr_catalogo AS nr
          ON nr.codigo_nr = evento.nr_id
       WHERE participacao.usuario_id = %s
         {status_filter}
    """
    query = f"""
        SELECT evento.titulo,
               evento.descricao,
               evento.link_externo,
               evento.modo_conclusao,
               evento.evidencia_obrigatoria,
               evento.status,
               turma.nome,
               turma.data_inicial,
               turma.data_termino,
               nr.codigo_nr,
               nr.titulo,
               COALESCE(conclusao.status, 'PENDENTE'),
               conclusao.data_conclusao,
               conclusao.data_validade,
               conclusao.data_validacao,
               conclusao.motivo_rejeicao
          {from_clause}
         ORDER BY turma.data_inicial, turma.id_turma
         LIMIT %s OFFSET %s
    """
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id_usuario FROM usuario WHERE firebase_uid = %s LIMIT 1",
                    [user.uid],
                )
                profile = cursor.fetchone()
                if profile is None:
                    return {
                        "status": "sem_perfil",
                        "mensagem": "Usuario autenticado nao encontrado no cadastro funcional.",
                    }
                user_id = profile[0]
                cursor.execute(f"SELECT COUNT(*) {from_clause}", [user_id])
                total = cursor.fetchone()[0]
                if total:
                    cursor.execute(query, [user_id, limite, (pagina - 1) * limite])
                    rows = cursor.fetchall()
                else:
                    rows = []
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta de treinamentos indisponivel."}

    trainings = []
    for row in rows:
        trainings.append({
            "titulo": row[0],
            "descricao": row[1][:300] if row[1] else None,
            "link_externo": row[2],
            "modo_conclusao": row[3],
            "evidencia_obrigatoria": row[4],
            "status_evento": row[5],
            "turma": row[6],
            "data_inicio": _valor_publico(row[7]),
            "data_termino": _valor_publico(row[8]),
            "nr": {"numero": row[9], "titulo": row[10]} if row[9] is not None else None,
            "status_participacao": row[11],
            "data_conclusao": _valor_publico(row[12]),
            "data_validade": _valor_publico(row[13]),
            "data_validacao": _valor_publico(row[14]),
            "motivo_rejeicao": row[15][:300] if row[15] else None,
        })
    return {
        "status": "ok" if total else "sem_dados",
        "situacao": situacao,
        "pagina": pagina,
        "limite": limite,
        "total": total,
        "total_paginas": (total + limite - 1) // limite,
        "treinamentos": trainings,
    }


@tool("consultar_google_calendar", args_schema=ConsultarEventosGoogleArgs)
async def consultar_google_calendar(
    inicio: datetime,
    fim: datetime,
    limite: int = 10,
    config: RunnableConfig = None,
) -> dict:
    """Consulta somente o calendário principal conectado pelo usuário autenticado."""
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    return await get_google_calendar_mcp_client().call_tool(
        "google_calendar_list_events",
        {
            "firebase_uid": user.uid,
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat(),
            "limite": limite,
        },
    )


@tool("criar_evento_google_calendar", args_schema=CriarEventoGoogleArgs)
async def criar_evento_google_calendar(
    titulo: str,
    inicio: datetime,
    fim: datetime,
    descricao: str | None = None,
    confirmar: bool = False,
    config: RunnableConfig = None,
) -> dict:
    """Prepara e, depois da confirmação explícita, cria um evento via MCP.

    A conta Google não é exigida para usar o chat. Quando ainda não houver uma
    conexão, a prévia fica pendente e a ferramenta devolve a rota OAuth que o
    cliente deve abrir antes de o usuário confirmar novamente.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    configurable = _configuravel(config)
    session_id = configurable.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {"status": "erro", "mensagem": "Sessao nao identificada no contexto."}

    if not confirmar:
        fuso = configurable.get("fuso", "America/Sao_Paulo")
        pending = _rascunho_evento(
            session_id=session_id,
            titulo=titulo,
            inicio=inicio,
            fim=fim,
            descricao=descricao,
            fuso=fuso,
        )
        connection = await get_google_calendar_mcp_client().call_tool(
            "google_calendar_status", {"firebase_uid": user.uid},
        )
        if connection.get("status") != "ok":
            return connection
        status = "aguardando_confirmacao" if connection.get("conectado") else "conexao_necessaria"
        result = {
            "status": status,
            "evento": {
                "titulo": titulo,
                "inicio": inicio.isoformat(),
                "fim": fim.isoformat(),
                "descricao": descricao,
                "fuso": fuso,
            },
            "acao_pendente": pending,
        }
        if status == "conexao_necessaria":
            result.update({
                "mensagem": "Conecte sua conta Google Calendar para continuar.",
                "rota_conexao": "/integracoes/google-calendar/conectar",
            })
        return result

    pending = configurable.get("acao_pendente")
    if not isinstance(pending, dict) or not _confirmacao_evento_valida(
        configurable,
        pending,
        session_id=session_id,
        titulo=titulo,
        inicio=inicio,
        fim=fim,
        descricao=descricao,
    ):
        return {
            "status": "confirmacao_invalida",
            "mensagem": "Prepare o evento novamente antes de confirmar.",
        }
    result = await get_google_calendar_mcp_client().call_tool(
        "google_calendar_create_event",
        {
            "firebase_uid": user.uid,
            "id_evento": pending["id_evento"],
            "titulo": titulo,
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat(),
            "fuso": pending["fuso"],
            "descricao": descricao,
        },
    )
    if result.get("status") == "conexao_necessaria":
        result["acao_pendente"] = pending
    return result


TOOLS_AGENDA = [
    consultar_treinamentos,
    consultar_google_calendar,
    criar_evento_google_calendar,
]

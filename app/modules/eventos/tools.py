from datetime import date, datetime
from typing import Literal

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import config as app_config
from app.core.security import CurrentUser
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


class EventosToolDecision(BaseModel):
    """Decisão do agente de Eventos entre consultar treinamentos e esclarecer."""

    model_config = ConfigDict(extra="forbid")

    acao: Literal["consultar_treinamentos", "responder"]
    filtros: ConsultarTreinamentosArgs | None = None
    resposta: SpecialistResult | None = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_filtros_ausentes(cls, data):
        if isinstance(data, dict) and data.get("acao") == "consultar_treinamentos" \
                and data.get("filtros") is None:
            return {**data, "filtros": {}}
        return data

    @model_validator(mode="after")
    def validar_acao(self):
        if self.acao == "consultar_treinamentos" and (
            self.filtros is None or self.resposta is not None
        ):
            raise ValueError("Consulta exige filtros e nao aceita resposta direta.")
        if self.acao == "responder" and (self.resposta is None or self.filtros is not None):
            raise ValueError("Resposta direta exige resultado e nao aceita filtros.")
        if self.resposta is not None and self.resposta.dominio != "eventos":
            raise ValueError("Resposta deve pertencer ao dominio de eventos.")
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


TOOLS_EVENTOS = [consultar_treinamentos]

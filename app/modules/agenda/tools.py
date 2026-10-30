from datetime import date, datetime
from typing import Literal

import psycopg
from app.infrastructure.database.connections import read_only_connection
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


class ConsultarEventosArgs(BaseModel):
    """Eventos internos atribuídos ao próprio usuário, sem exigir OAuth."""

    model_config = ConfigDict(extra="forbid")
    proximos: bool = True
    pagina: int = Field(default=1, ge=1, le=1000)
    limite: int = Field(default=5, ge=1, le=10)


class AgendaToolDecision(BaseModel):
    """Decisão de Agenda para consultar eventos internos e treinamentos."""

    model_config = ConfigDict(extra="forbid")

    acao: Literal[
        "consultar_eventos",
        "consultar_treinamentos",
        "responder",
    ]
    filtros: (
        ConsultarTreinamentosArgs
        | ConsultarEventosArgs
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
        # Recupera somente uma pergunta já presente numa resposta de Agenda.
        # Isso não autoriza tools nem corrige decisões de criação inválidas;
        # todos os demais campos continuam sujeitos à validação completa.
        response = data.get("resposta")
        if (
            action == "responder" and filters is None and isinstance(response, dict)
            and response.get("dominio") == "agenda"
            and response.get("status") == "esclarecer"
            and response.get("esclarecer") is None
        ):
            question = response.get("resposta")
            if isinstance(question, str) and "?" in question and 0 < len(question.strip()) <= 1000:
                data = {**data, "resposta": {**response, "esclarecer": question.strip()}}
        if action in {"consultar_treinamentos", "consultar_eventos"} and filters is None:
            filters = {}
        schemas = {
            "consultar_eventos": ConsultarEventosArgs,
            "consultar_treinamentos": ConsultarTreinamentosArgs,
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
            "consultar_eventos": ConsultarEventosArgs,
            "consultar_treinamentos": ConsultarTreinamentosArgs,
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
    return read_only_connection(app_config.DATABASE_URL)


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
    return _consultar_participacoes(situacao, pagina, limite, config)


def _consultar_participacoes(situacao, pagina, limite, config, *, proximos=False):
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
    if proximos:
        status_filter += (
            " AND evento.status = 'ATIVO'"
            " AND turma.data_inicial >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')"
        )
    from_clause = f"""
        FROM turma_colaborador AS participacao
        JOIN turma
          ON turma.id_turma = participacao.turma_id
        JOIN evento
          ON evento.id_evento = turma.evento_id
        LEFT JOIN conclusao_evento AS conclusao
          ON conclusao.turma_colaborador_id = participacao.id_turma_colaborador
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


@tool("consultar_eventos", args_schema=ConsultarEventosArgs)
def consultar_eventos(
    proximos: bool = True, pagina: int = 1, limite: int = 5,
    config: RunnableConfig = None,
) -> dict:
    """Consulta eventos do Astro com inscrição do próprio usuário.

    Datas pertencem às turmas. Próximos inclui somente eventos ativos com início
    futuro, independentemente da conclusão; nunca lista eventos de terceiros.
    """
    result = _consultar_participacoes("todos", pagina, limite, config, proximos=proximos)
    result["proximos"] = proximos
    result["eventos"] = result.pop("treinamentos", [])
    if result.get("mensagem"):
        result["mensagem"] = result["mensagem"].replace("treinamentos", "eventos atribuídos")
    return result


@tool("consultar_grafico_treinamentos")
def consultar_grafico_treinamentos(config: RunnableConfig = None) -> dict:
    """Conta as participações do usuário autenticado por status de conclusão."""
    user = _usuario_do_contexto(config)
    if user is None or user.role == "ADMIN":
        return {"status": "nao_autorizado", "mensagem": "Não foi possível consultar treinamentos para esta conta."}
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de treinamentos indisponível."}
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id_usuario FROM usuario WHERE firebase_uid = %s LIMIT 1", [user.uid])
                profile = cursor.fetchone()
                if not profile:
                    return {"status": "sem_perfil", "mensagem": "Usuário não encontrado no cadastro funcional."}
                cursor.execute("""
                    SELECT COALESCE(conclusao.status, 'PENDENTE'), COUNT(*)
                    FROM turma_colaborador participacao
                    JOIN turma ON turma.id_turma = participacao.turma_id
                    JOIN evento ON evento.id_evento = turma.evento_id
                    LEFT JOIN conclusao_evento conclusao
                      ON conclusao.turma_colaborador_id = participacao.id_turma_colaborador
                    WHERE participacao.usuario_id = %s AND evento.status = 'ATIVO'
                    GROUP BY COALESCE(conclusao.status, 'PENDENTE')
                """, [profile[0]])
                rows = cursor.fetchall()
        allowed = {"PENDENTE": "Pendentes", "CONCLUIDO": "Concluídos", "REJEITADO": "Rejeitados"}
        counts = {label: 0 for label in allowed.values()}
        for status, count in rows:
            if status not in allowed or type(count) is not int or count < 0:
                return {"status": "indisponivel", "mensagem": "Não foi possível validar os estados dos treinamentos."}
            counts[allowed[status]] += count
        return {"status": "ok" if sum(counts.values()) else "sem_dados", "categorias": counts,
                "fonte": "Participações em treinamentos ativos atribuídas ao usuário"}
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta de treinamentos indisponível."}


@tool("consultar_evolucao_treinamentos")
def consultar_evolucao_treinamentos(config: RunnableConfig = None) -> dict:
    """Contagem mensal de conclusões pessoais nos últimos 12 meses civis."""
    user = _usuario_do_contexto(config)
    if user is None or user.role == "ADMIN":
        return {"status": "nao_autorizado", "mensagem": "Não foi possível consultar treinamentos para esta conta."}
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de treinamentos indisponível."}
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id_usuario FROM usuario WHERE firebase_uid = %s LIMIT 1", [user.uid])
                profile = cursor.fetchone()
                if not profile:
                    return {"status": "sem_perfil", "mensagem": "Usuário não encontrado no cadastro funcional."}
                cursor.execute("""
                    WITH meses AS (
                      SELECT generate_series(date_trunc('month', CURRENT_DATE) - interval '11 months',
                                             date_trunc('month', CURRENT_DATE), interval '1 month') AS mes
                    ), concluidos AS (
                      SELECT date_trunc('month', conclusao.data_conclusao) AS mes, COUNT(*) AS total
                      FROM turma_colaborador participacao
                      JOIN turma ON turma.id_turma = participacao.turma_id
                      JOIN evento ON evento.id_evento = turma.evento_id
                      JOIN conclusao_evento conclusao ON conclusao.turma_colaborador_id = participacao.id_turma_colaborador
                      WHERE participacao.usuario_id = %s AND evento.status = 'ATIVO'
                        AND conclusao.status = 'CONCLUIDO' AND conclusao.data_conclusao IS NOT NULL
                        AND conclusao.data_conclusao >= date_trunc('month', CURRENT_DATE) - interval '11 months'
                        AND conclusao.data_conclusao < date_trunc('month', CURRENT_DATE) + interval '1 month'
                      GROUP BY 1
                    )
                    SELECT to_char(meses.mes, 'YYYY-MM'), COALESCE(concluidos.total, 0)
                    FROM meses LEFT JOIN concluidos ON concluidos.mes = meses.mes ORDER BY meses.mes
                """, [profile[0]])
                rows = cursor.fetchall()
        import re
        if len(rows) != 12 or any(not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month)
                                  or type(count) is not int or count < 0 for month, count in rows):
            raise ValueError("invalid_monthly_series")
        months = [month for month, _ in rows]
        if len(set(months)) != 12 or months != sorted(months):
            raise ValueError("invalid_monthly_series")
        return {"status": "ok" if sum(count for _, count in rows) else "sem_dados",
                "categorias": dict(rows), "fonte": "Conclusões pessoais de treinamentos em eventos ativos",
                "tipo_grafico": "grafico_linhas"}
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta de evolução de treinamentos indisponível."}


TOOLS_AGENDA = [
    consultar_evolucao_treinamentos,
    consultar_grafico_treinamentos,
    consultar_eventos,
    consultar_treinamentos,
]

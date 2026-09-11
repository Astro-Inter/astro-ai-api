from typing import Literal

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core import config as app_config
from app.core.security import CurrentUser
from app.modules.chat.schemas import SpecialistResult


UserStatus = Literal["ATIVO", "PRE_CADASTRADO", "DESATIVADO"]
UserType = Literal["GESTOR", "GESTOR_WORKSPACE", "FUNCIONARIO"]
MANAGER_VISIBLE_TYPES = ["GESTOR", "FUNCIONARIO"]
WORKSPACE_MANAGER_VISIBLE_TYPES = ["GESTOR", "GESTOR_WORKSPACE", "FUNCIONARIO"]

OTHER_USER_COLUMNS = ("nome", "email", "tipo", "cargo", "unidade", "modalidade", "status")
CURRENT_USER_COLUMNS = (
    "nome", "email", "cpf", "tipo", "cargo", "unidade", "modalidade", "status",
    "criado_em",
)


class BuscarOutrosUsuariosArgs(BaseModel):
    """Filtros que o agente pode enviar para consultar outros usuários."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: list[UserStatus] = Field(
        default_factory=list,
        max_length=3,
        description="Status a consultar: ATIVO, PRE_CADASTRADO ou DESATIVADO.",
    )
    tipos: list[UserType] = Field(
        default_factory=list,
        max_length=3,
        description="Perfis a consultar: GESTOR, GESTOR_WORKSPACE ou FUNCIONARIO.",
    )
    nome: str | None = Field(
        default=None, min_length=1, max_length=255,
        description="Trecho do nome do funcionário, sem curingas SQL.",
    )
    cargo: str | None = Field(
        default=None, min_length=1, max_length=255,
        description="Trecho do nome do cargo, sem curingas SQL.",
    )
    limite: int = Field(
        default=20, ge=1, le=50,
        description="Quantidade máxima de usuários retornados, entre 1 e 50.",
    )

    @field_validator("status", "tipos")
    @classmethod
    def sem_valores_repetidos(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Filtros repetidos nao sao permitidos.")
        return values


class RhToolDecision(BaseModel):
    """Decisão do agente entre responder ou usar uma consulta de RH."""

    model_config = ConfigDict(extra="forbid")

    acao: Literal["buscar_outros_usuarios", "buscar_meus_dados", "responder"]
    filtros: BuscarOutrosUsuariosArgs | None = None
    resposta: SpecialistResult | None = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_filtros_vazios(cls, data):
        """Aceita o formato vazio que alguns provedores geram para campos opcionais."""
        if not isinstance(data, dict):
            return data
        if data.get("acao") == "buscar_outros_usuarios" and data.get("filtros") is None:
            data = dict(data)
            data["filtros"] = {}
            return data
        if data.get("acao") != "buscar_meus_dados":
            return data
        filters = data.get("filtros")
        if filters in (None, {}):
            data = dict(data)
            data["filtros"] = None
            return data
        if isinstance(filters, dict):
            empty_filters = {
                "status": [], "tipos": [], "nome": None, "cargo": None, "limite": 20,
            }
            if {**empty_filters, **filters} == empty_filters:
                data = dict(data)
                data["filtros"] = None
        return data

    @model_validator(mode="after")
    def validar_acao(self):
        if self.acao == "buscar_outros_usuarios" and (
            self.filtros is None or self.resposta is not None
        ):
            raise ValueError("A busca de outros usuarios exige filtros e nao aceita resposta.")
        if self.acao == "buscar_meus_dados" and (
            self.filtros is not None or self.resposta is not None
        ):
            raise ValueError("A busca dos dados pessoais nao aceita filtros nem resposta.")
        if self.acao == "responder" and (self.resposta is None or self.filtros is not None):
            raise ValueError("A resposta direta exige resultado e nao aceita filtros.")
        if self.resposta is not None and self.resposta.dominio != "rh":
            raise ValueError("A resposta deve pertencer ao dominio de RH.")
        return self


def get_conn():
    """Abre uma conexão de leitura curta com o PostgreSQL."""
    return psycopg.connect(
        app_config.DATABASE_URL,
        autocommit=True,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c default_transaction_read_only=on",
    )


def _usuario_do_contexto(config: RunnableConfig) -> CurrentUser | None:
    configuravel = (config or {}).get("configurable", {})
    raw_user = configuravel.get("usuario_atual")
    try:
        return raw_user if isinstance(raw_user, CurrentUser) else CurrentUser.model_validate(raw_user)
    except Exception:
        return None


def _filtro_ilike(value: str) -> str:
    """Transforma texto em busca literal por trecho, escapando curingas do LIKE."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@tool("buscar_outros_usuarios", args_schema=BuscarOutrosUsuariosArgs)
def buscar_outros_usuarios(
    status: list[UserStatus] | None = None,
    tipos: list[UserType] | None = None,
    nome: str | None = None,
    cargo: str | None = None,
    limite: int = 20,
    config: RunnableConfig = None,
) -> dict:
    """Consulta dados profissionais de outros usuários visíveis ao usuário atual.

    Use para pesquisar nome, e-mail, perfil, cargo, unidade, modalidade e status.
    A identidade e o escopo de acesso vêm do contexto autenticado da requisição;
    nunca peça nem invente um Firebase UID.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if user.role == "FUNCIONARIO":
        return {
            "status": "nao_autorizado",
            "mensagem": "Seu perfil nao permite consultar outros usuarios.",
        }
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de usuarios indisponivel."}

    query = """
        SELECT usuarios.nome AS nome,
               usuarios.email AS email,
               usuarios.tipo AS tipo,
               cargos.nome AS cargo,
               unidades.nome AS unidade,
               usuarios.modalidade AS modalidade,
               usuarios.status AS status
          FROM usuarios
          JOIN cargos ON cargos.id_cargo = usuarios.cargo_id
          JOIN unidades ON unidades.id_unidade = usuarios.unidade_id
         WHERE 1=1
    """
    parameters = []

    if user.role == "GESTOR_WORKSPACE":
        query += """
            AND unidades.workspace_id = (
                SELECT unidade_atual.workspace_id
                  FROM usuarios AS usuario_atual
                  JOIN unidades AS unidade_atual
                    ON unidade_atual.id_unidade = usuario_atual.unidade_id
                 WHERE usuario_atual.firebase_uid = %s
                 LIMIT 1
            )
            AND usuarios.tipo = ANY(%s)
        """
        parameters.extend([user.uid, WORKSPACE_MANAGER_VISIBLE_TYPES])
    elif user.role == "GESTOR":
        query += """
            AND usuarios.unidade_id = (
                SELECT unidade_id
                  FROM usuarios
                 WHERE firebase_uid = %s
                 LIMIT 1
            )
            AND usuarios.tipo = ANY(%s)
        """
        parameters.extend([user.uid, MANAGER_VISIBLE_TYPES])
    # Esta consulta nunca devolve o próprio usuário; os dados pessoais têm tool dedicada.
    query += " AND usuarios.firebase_uid <> %s"
    parameters.append(user.uid)
    if status:
        query += " AND usuarios.status = ANY(%s)"
        parameters.append(list(status))
    if tipos:
        query += " AND usuarios.tipo = ANY(%s)"
        parameters.append(list(tipos))
    if nome:
        query += " AND usuarios.nome ILIKE %s ESCAPE '\\'"
        parameters.append(_filtro_ilike(nome))
    if cargo:
        query += " AND cargos.nome ILIKE %s ESCAPE '\\'"
        parameters.append(_filtro_ilike(cargo))

    query += " ORDER BY usuarios.nome, usuarios.email LIMIT %s"
    parameters.append(limite)

    try:
        with get_conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()
    except Exception:
        # Detalhes do driver, da URL e do SQL nunca são devolvidos ao modelo.
        return {"status": "indisponivel", "mensagem": "Consulta de usuarios indisponivel."}

    usuarios = [dict(zip(OTHER_USER_COLUMNS, row)) for row in rows]
    return {
        "status": "ok" if usuarios else "sem_dados",
        "quantidade": len(usuarios),
        "usuarios": usuarios,
    }

@tool("buscar_meus_dados")
def buscar_meus_dados(config: RunnableConfig = None) -> dict:
    """Retorna somente os dados pessoais e profissionais do usuário autenticado.

    A identidade vem do contexto seguro da requisição. A ferramenta não recebe UID,
    nome, e-mail ou qualquer outro seletor controlado pelo modelo ou pelo usuário.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de dados pessoais indisponivel."}

    if user.role == "ADMIN":
        query = """
            SELECT admin.nome AS nome,
                   admin.email AS email
              FROM admin
             WHERE admin.firebase_uid = %s
             LIMIT 1
        """
    else:
        query = """
            SELECT usuarios.nome AS nome,
                   usuarios.email AS email,
                   usuarios.cpf AS cpf,
                   usuarios.tipo AS tipo,
                   cargos.nome AS cargo,
                   unidades.nome AS unidade,
                   usuarios.modalidade AS modalidade,
                   usuarios.status AS status,
                   usuarios.criado_em AS criado_em
              FROM usuarios
              JOIN cargos ON cargos.id_cargo = usuarios.cargo_id
              JOIN unidades ON unidades.id_unidade = usuarios.unidade_id
             WHERE usuarios.firebase_uid = %s
             LIMIT 1
        """
    try:
        with get_conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, [user.uid])
                row = cursor.fetchone()
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta de dados pessoais indisponivel."}

    if row is None:
        return {"status": "sem_dados", "dados": None}
    if user.role == "ADMIN":
        data = dict.fromkeys(CURRENT_USER_COLUMNS)
        data.update({"nome": row[0], "email": row[1], "tipo": "ADMIN"})
    else:
        data = dict(zip(CURRENT_USER_COLUMNS, row))
    if hasattr(data["criado_em"], "isoformat"):
        data["criado_em"] = data["criado_em"].isoformat()
    return {"status": "ok", "dados": data}


TOOLS_RH = [buscar_outros_usuarios, buscar_meus_dados]

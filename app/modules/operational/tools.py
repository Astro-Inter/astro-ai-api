"""Consultas operacionais limitadas, sem escrita nem decisões de autorização por LLM."""
import asyncio
from typing import Literal
from urllib.parse import quote
from datetime import date, datetime

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core import config as app_config
from app.core.security import CurrentUser
from app.infrastructure.database.connections import read_only_connection
from app.infrastructure.astro_queries import read_api

MANAGERS = {"GESTOR", "GESTOR_WORKSPACE"}


class TeamArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agrupar_por: Literal["cargo", "unidade", "perfil", "situacao"] = "cargo"
    tipo_grafico: Literal["grafico_barras", "grafico_setores", "grafico_linhas"] = "grafico_barras"
    tipos: list[Literal["GESTOR", "GESTOR_WORKSPACE", "COLABORADOR"]] | None = Field(default=None, min_length=1, max_length=3)
    status: list[Literal["ATIVO", "PRE_CADASTRADO", "DESATIVADO"]] | None = Field(default=None, min_length=1, max_length=3)
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    cargo: str | None = Field(default=None, min_length=1, max_length=120)
    unidade: str | None = Field(default=None, min_length=1, max_length=200)
    nr: int | None = Field(default=None, ge=1, le=100)
    dias: int = Field(default=30, ge=1, le=365)
    incluir_vencidas: bool = False
    pagina: int = Field(default=1, ge=1, le=1000)
    limite: int = Field(default=20, ge=1, le=50)


class ApiArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    identificador: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    unidade: str | None = Field(default=None, min_length=1, max_length=200)
    nr: int | None = Field(default=None, ge=1, le=100)
    pagina: int = Field(default=1, ge=1, le=1000)
    limite: int = Field(default=20, ge=1, le=50)


class OperationalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acao: Literal["consultar_indicadores_equipe", "consultar_grafico_pendencias_nr", "consultar_grafico_vencimentos", "consultar_vencimentos_conformidades", "consultar_pendencias_equipe", "consultar_conformidade_unidade", "consultar_formularios_pendentes", "consultar_respostas_formulario", "consultar_detalhes_evento", "consultar_candidatos_treinamento"]
    filtros: TeamArgs | ApiArgs

    @model_validator(mode="before")
    @classmethod
    def filters_for_action(cls, data):
        if isinstance(data, dict):
            schema = TeamArgs if data.get("acao") in TEAM_ACTIONS else ApiArgs
            return {**data, "filtros": schema.model_validate(data.get("filtros", {}))}
        return data


TEAM_ACTIONS = {"consultar_indicadores_equipe", "consultar_grafico_pendencias_nr", "consultar_grafico_vencimentos", "consultar_vencimentos_conformidades", "consultar_pendencias_equipe"}


def user_from(config):
    try:
        return CurrentUser.model_validate((config or {}).get("configurable", {}).get("usuario_atual"))
    except ValueError:
        return None


def denied():
    return {"status":"nao_autorizado", "mensagem":"Seu perfil não permite esta consulta."}


def scalar(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value[:500] if isinstance(value, str) else value
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    raise ValueError("Campo não escalar")


def table(title, rows, columns, total, page, limit):
    import re
    def key_for(key):
        return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
    return {"status":"ok" if rows else "sem_dados", "titulo":title,
            "colunas":[{"chave":key_for(key),"titulo":label} for key,label in columns],
            "linhas":[{key_for(key):scalar(row.get(key)) for key,_ in columns} for row in rows],
            "total":total,"pagina":page,"total_paginas":max(1,(total+limit-1)//limit),
            "fonte":"Consulta autorizada ao cadastro Astro", "completo":page == 1 and total <= limit}


# Revalidates the viewer's active role AND workspace in the database on every query.
SCOPE = """
FROM usuario target JOIN unidade unit ON unit.id_unidade = target.unidade_id
JOIN usuario viewer ON viewer.firebase_uid = %s AND viewer.tipo = %s AND viewer.status = 'ATIVO'
JOIN unidade viewer_unit ON viewer_unit.id_unidade = viewer.unidade_id
JOIN cargo ON cargo.id_cargo = target.cargo_id
WHERE unit.workspace_id = viewer_unit.workspace_id
AND unit.id_unidade IN (SELECT id_unidade FROM fn_seleciona_unidades(%s))
AND (viewer.tipo = 'GESTOR_WORKSPACE' OR target.unidade_id = viewer.unidade_id)
AND target.tipo = ANY(%s) AND target.id_usuario <> viewer.id_usuario
"""


def team_read(action, args, user):
    if action in {"consultar_indicadores_equipe", "consultar_grafico_pendencias_nr", "consultar_grafico_vencimentos"} and args.tipo_grafico == "grafico_linhas":
        return {"status":"esclarecer", "mensagem":"Os indicadores de equipe representam o cadastro atual, sem série histórica mensal. Posso mostrar barras ou setores; linhas estão disponíveis para suas conclusões de treinamentos."}
    if action == "consultar_grafico_vencimentos" and (args.dias != 30 or args.incluir_vencidas):
        return {"status":"esclarecer", "mensagem":"Este gráfico usa faixas fixas, incluindo vencidas e sem validade. Para período personalizado, consulte a lista de vencimentos."}
    if not app_config.DATABASE_URL:
        return {"status":"indisponivel","mensagem":"Consulta ao cadastro indisponível."}
    types = ["GESTOR", "COLABORADOR"] + (["GESTOR_WORKSPACE"] if user.role == "GESTOR_WORKSPACE" else [])
    params = [user.uid, user.role, user.uid, types]
    scope = SCOPE
    if args.tipos:
        scope += " AND target.tipo = ANY(%s)"
        params.append(args.tipos)
    if args.status:
        scope += " AND target.status = ANY(%s)"
        params.append(args.status)
    for field, value in (("target.nome", args.nome), ("cargo.nome", args.cargo)):
        if value:
            scope += f" AND {field} ILIKE %s ESCAPE E'\\\\'"
            params.append("%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if args.unidade:
        scope += " AND LOWER(unit.nome) = LOWER(%s)"
        params.append(args.unidade)
    if action == "consultar_indicadores_equipe":
        dimension = {"cargo":"cargo.nome", "unidade":"unit.nome", "perfil":"target.tipo", "situacao":"target.status"}[args.agrupar_por]
        query = f"WITH groups AS (SELECT {dimension} AS categoria, COUNT(*) AS quantidade " + scope + f" GROUP BY {dimension}) SELECT categoria, quantidade, COUNT(*) OVER() AS total FROM groups ORDER BY categoria LIMIT %s OFFSET %s"
        columns = [("categoria",args.agrupar_por.capitalize()),("quantidade","Quantidade")]
        title = "Distribuição da equipe por " + args.agrupar_por
    elif action == "consultar_grafico_vencimentos":
        query = """WITH people AS (SELECT target.id_usuario """ + scope + """),
records AS (
 SELECT p.id_usuario, v.nr_id, v.data_validade,
 CASE WHEN v.data_validade IS NULL THEN 5
      WHEN v.data_validade < CURRENT_DATE THEN 1
      WHEN v.data_validade <= CURRENT_DATE + 7 THEN 2
      WHEN v.data_validade <= CURRENT_DATE + 30 THEN 3 ELSE 4 END AS faixa
 FROM people p JOIN vw_conformidade_colaborador v ON v.usuario_id = p.id_usuario
 WHERE v.aplicavel IS TRUE AND (%s::integer IS NULL OR v.nr_id = %s)
), buckets(ordem, categoria) AS (VALUES
 (1, 'Vencidas'), (2, 'Hoje a 7 dias'), (3, '8 a 30 dias'),
 (4, 'Mais de 30 dias'), (5, 'Sem validade')),
aggregated AS (
 SELECT b.ordem, b.categoria, COUNT(r.id_usuario) AS quantidade,
 COUNT(DISTINCT r.id_usuario) AS pessoas_unicas
 FROM buckets b LEFT JOIN records r ON r.faixa = b.ordem
 GROUP BY b.ordem, b.categoria
)
SELECT categoria, quantidade, pessoas_unicas, CURRENT_DATE AS data_referencia,
       COUNT(*) OVER() AS total
FROM aggregated ORDER BY ordem LIMIT %s OFFSET %s"""
        params += [args.nr, args.nr]
        columns = [("categoria","Faixa de validade"),("quantidade","Registros de conformidade"),
                   ("pessoas_unicas","Pessoas únicas na faixa"),("data_referencia","Data de referência")]
        title = "Validades por faixa: registros de conformidade, não pessoas únicas"
    elif action == "consultar_vencimentos_conformidades":
        query = """WITH people AS (SELECT target.id_usuario, target.nome, target.email, unit.nome AS unidade """ + scope + """)
SELECT p.nome, p.email, p.unidade, v.nr_id AS nr, v.data_validade AS validade,
       v.data_validade - CURRENT_DATE AS dias_restantes, COUNT(*) OVER() AS total
FROM people p JOIN vw_conformidade_colaborador v ON v.usuario_id = p.id_usuario
WHERE v.aplicavel IS TRUE AND v.data_validade IS NOT NULL
AND v.data_validade <= CURRENT_DATE + %s AND (%s OR v.data_validade >= CURRENT_DATE)
AND (%s::integer IS NULL OR v.nr_id = %s)
ORDER BY v.data_validade, p.email, v.nr_id LIMIT %s OFFSET %s"""
        params += [args.dias, args.incluir_vencidas, args.nr, args.nr]
        columns = [("nome","Pessoa"),("email","E-mail"),("unidade","Unidade"),("nr","NR"),("validade","Validade"),("dias_restantes","Dias restantes")]
        title = "Vencimentos de conformidades registradas (sem datas nulas)"
    else:
        query = """WITH people AS (SELECT target.id_usuario, target.nome, target.email, target.cargo_id, target.unidade_id, unit.nome AS unidade """ + scope + """ AND target.status = 'ATIVO'),
records AS (SELECT p.id_usuario, p.nome, p.email, p.unidade, nr.codigo_nr AS nr,
CASE WHEN current_record.data_validade >= CURRENT_DATE THEN 'VIGENTE'
     WHEN EXISTS (SELECT 1 FROM turma_colaborador tc JOIN turma t ON t.id_turma = tc.turma_id
                  JOIN evento e ON e.id_evento = t.evento_id
                  LEFT JOIN conclusao_evento ce ON ce.turma_colaborador_id = tc.id_turma_colaborador
                  WHERE tc.usuario_id = p.id_usuario AND e.nr_id = nr.codigo_nr
                    AND e.status <> 'CANCELADO' AND COALESCE(ce.status,'PENDENTE') = 'PENDENTE') THEN 'PENDENTE'
     WHEN current_record.data_validade < CURRENT_DATE THEN 'RENOVACAO_NECESSARIA'
     ELSE 'REALIZACAO_NECESSARIA' END AS situacao
FROM people p JOIN cargo_nr cn ON cn.cargo_id = p.cargo_id
JOIN unidade_nr un ON un.unidade_id = p.unidade_id AND un.nr_id = cn.nr_id
JOIN nr_catalogo nr ON nr.codigo_nr = un.nr_id AND nr.revogada = FALSE
LEFT JOIN LATERAL (SELECT c.data_validade FROM conformidade c
                  WHERE c.usuario_id = p.id_usuario AND c.nr_id = nr.codigo_nr AND c.aplicavel IS TRUE
                  ORDER BY c.data_validade DESC NULLS LAST, c.id_conformidade DESC LIMIT 1) current_record ON TRUE
WHERE (%s::integer IS NULL OR nr.codigo_nr = %s))
SELECT nome, email, unidade, nr, situacao, COUNT(*) OVER() AS total FROM records
WHERE situacao <> 'VIGENTE' ORDER BY email, nr LIMIT %s OFFSET %s"""
        params += [args.nr, args.nr]
        columns = [("nome","Pessoa"),("email","E-mail"),("unidade","Unidade"),("nr","NR"),("situacao","Situação")]
        title = "Pendências cadastrais da equipe (não é certificação legal)"
    if action == "consultar_grafico_pendencias_nr":
        # Reuse the same authorized population and status rules as the detailed tool.
        query = query[:query.index("SELECT nome, email, unidade, nr, situacao, COUNT(*) OVER() AS total FROM records")]
        query += """, aggregated AS (
 SELECT nr, COUNT(*) AS quantidade, COUNT(DISTINCT id_usuario) AS pessoas_unicas
 FROM records WHERE situacao <> 'VIGENTE' GROUP BY nr
)
SELECT 'NR-' || nr::text AS categoria, quantidade, pessoas_unicas,
       CURRENT_DATE AS data_referencia, COUNT(*) OVER() AS total
FROM aggregated ORDER BY nr LIMIT %s OFFSET %s"""
        columns = [("categoria","NR"),("quantidade","Pendências pessoa/NR"),
                   ("pessoas_unicas","Pessoas únicas nesta NR"),("data_referencia","Data de referência")]
        title = "Pendências por NR: uma pessoa pode aparecer em mais de uma NR"
    params += [args.limite, (args.pagina-1)*args.limite]
    try:
        with read_only_connection(app_config.DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        # An empty out-of-range page has no window count; do not claim a zero total.
        if not rows:
            return {"status":"sem_dados","mensagem":"Não há registros nesta página; isso não comprova ausência em outras páginas."}
        data = [dict(zip([key for key,_ in columns], row[:-1])) for row in rows]
        result = table(title, data, columns, rows[0][-1], args.pagina, args.limite)
        if action in {"consultar_indicadores_equipe", "consultar_grafico_pendencias_nr", "consultar_grafico_vencimentos"}:
            result["unidade_grafico"] = {"consultar_indicadores_equipe":"pessoas", "consultar_grafico_pendencias_nr":"pendências", "consultar_grafico_vencimentos":"registros"}[action]
            if action != "consultar_indicadores_equipe":
                result["aviso"] = "Pessoas únicas são contadas por categoria; não some essas contagens como pessoas únicas globais."
            if result["completo"] and len(data) <= 12:
                result["grafico"] = [{"categoria":row["categoria"],"valor":row["quantidade"]} for row in data]
                result["tipo_grafico"] = args.tipo_grafico
            else:
                result["aviso"] = "O gráfico exige todas as categorias e suporta até 12; consulte a tabela paginada."
        return result
    except Exception:
        return {"status":"indisponivel","mensagem":"Não foi possível consultar o cadastro. Nenhum resultado foi confirmado."}


async def execute_team(action, args, config):
    user = user_from(config)
    if user is None or user.role not in MANAGERS:
        return denied()
    return await asyncio.to_thread(team_read, action, args, user)


@tool("consultar_indicadores_equipe", args_schema=TeamArgs)
async def consultar_indicadores_equipe(config: RunnableConfig = None, **kwargs) -> dict:
    """Agrupa a equipe autorizada por cargo, unidade, perfil ou situação, antes de paginar."""
    return await execute_team("consultar_indicadores_equipe", TeamArgs.model_validate(kwargs), config)


@tool("consultar_vencimentos_conformidades", args_schema=TeamArgs)
async def consultar_vencimentos_conformidades(config: RunnableConfig = None, **kwargs) -> dict:
    """Lista conformidades aplicáveis com data de validade, opcionalmente vencidas."""
    return await execute_team("consultar_vencimentos_conformidades", TeamArgs.model_validate(kwargs), config)


@tool("consultar_pendencias_equipe", args_schema=TeamArgs)
async def consultar_pendencias_equipe(config: RunnableConfig = None, **kwargs) -> dict:
    """Consulta NRs de cargo/unidade pendentes, a realizar ou renovar na equipe autorizada."""
    return await execute_team("consultar_pendencias_equipe", TeamArgs.model_validate(kwargs), config)


@tool("consultar_grafico_pendencias_nr", args_schema=TeamArgs)
async def consultar_grafico_pendencias_nr(config: RunnableConfig = None, **kwargs) -> dict:
    """Agrega pendências pessoa/NR e pessoas distintas por NR no escopo autorizado."""
    return await execute_team("consultar_grafico_pendencias_nr", TeamArgs.model_validate(kwargs), config)


@tool("consultar_grafico_vencimentos", args_schema=TeamArgs)
async def consultar_grafico_vencimentos(config: RunnableConfig = None, **kwargs) -> dict:
    """Agrega conformidades em faixas fixas não sobrepostas, incluindo validade ausente."""
    return await execute_team("consultar_grafico_vencimentos", TeamArgs.model_validate(kwargs), config)


async def execute_api(action, args, config):
    user = user_from(config)
    allowed = MANAGERS | ({"COLABORADOR"} if action == "consultar_detalhes_evento" else set())
    if user is None or user.role not in allowed:
        return denied()
    if action == "consultar_respostas_formulario" and not args.identificador:
        return {"status":"esclarecer","mensagem":"Posso resumir o preenchimento de uma instância concluída, mas não contar pessoas de um modelo de formulário. Selecione o formulário desejado."}
    if action == "consultar_detalhes_evento" and not args.identificador:
        return {"status":"esclarecer","mensagem":"Selecione o evento desejado em um resultado anterior; não vou adivinhá-lo."}
    if action == "consultar_respostas_formulario":
        from uuid import UUID
        try:
            if str(UUID(args.identificador)) != args.identificador.lower():
                raise ValueError()
        except ValueError:
            return {"status":"esclarecer", "mensagem":"Selecione um formulário com referência UUID válida."}
    if action == "consultar_detalhes_evento" and (not args.identificador.isdecimal() or int(args.identificador) <= 0):
        return {"status":"esclarecer","mensagem":"Selecione um evento válido."}
    identifier = quote(args.identificador or "", safe="")
    selected_nr = args.nr
    if action == "consultar_candidatos_treinamento" and args.identificador:
        if not args.identificador.isdecimal() or int(args.identificador) <= 0:
            return {"status":"esclarecer", "mensagem":"Selecione um evento válido."}
        event_result = await read_api(f"/events/{identifier}", {})
        if event_result.get("status") != "ok":
            return event_result
        event = event_result.get("data")
        if not isinstance(event,dict) or (event.get("nrId") is not None and (type(event["nrId"]) is not int or event["nrId"] <= 0)):
            return {"status":"indisponivel", "mensagem":"Evento incompatível com a consulta."}
        if args.nr is not None and args.nr != event.get("nrId"):
            return {"status":"esclarecer", "mensagem":"A NR informada difere do evento selecionado."}
        selected_nr = event.get("nrId")
    params = {}
    if action == "consultar_conformidade_unidade":
        path = "/conformities/units"
        columns = [("unitName","Unidade"),("nrId","NR"),("nrTitle","Título"),("expiresAt","Validade"),("status","Situação"),("origin","Origem")]
    elif action == "consultar_formularios_pendentes":
        path = "/forms/received"
        params = {"status":"PENDENTE", "page":args.pagina-1,"size":args.limite}
        columns = [("id","Referência"),("title","Formulário"),("dueDate","Prazo"),("status","Situação"),("questionCount","Questões")]
    elif action == "consultar_respostas_formulario":
        path = f"/forms/{identifier}/responses"
        columns = [("question","Pergunta"),("type","Tipo"),("respondida","Respondida")]
    elif action == "consultar_candidatos_treinamento":
        path = "/events/eligible-candidates"
        params = {"nrId":selected_nr} if selected_nr else {}
        columns = [("name","Nome"),("email","E-mail"),("roleName","Cargo"),("unitName","Unidade")]
    else:
        path = f"/events/collaborator/{identifier}" if user.role == "COLABORADOR" else f"/events/{identifier}"
        columns = [("campo","Campo"),("valor","Valor")]
    response = await read_api(path, params)
    if response.get("status") != "ok":
        return response
    data = response["data"]
    try:
        if action == "consultar_formularios_pendentes":
            rows, total = data["items"], data["totalElements"]
            if not isinstance(rows, list) or not isinstance(total, int) or total < 0 or len(rows) > args.limite:
                raise ValueError()
        elif action == "consultar_respostas_formulario":
            # No location readings, answers, attachments or reviewer identifiers sent to LLM.
            rows = [{"question":q["question"],"type":q["type"],"respondida":"Sim" if q.get("answer") else "Não"} for q in data["form"]["questions"]]
            total = len(rows)
            rows = rows[(args.pagina-1)*args.limite:args.pagina*args.limite]
        elif action == "consultar_detalhes_evento":
            fields = ("eventTitle","eventDescription","eventStatus","classLimitDate","participationStatus","classStatus","complianceValidity") if user.role == "COLABORADOR" else ("title","description","status","nrId","completionMode","mandatoryEvidence")
            labels = {"eventTitle":"Título", "title":"Título", "eventDescription":"Descrição", "description":"Descrição", "eventStatus":"Situação do evento", "status":"Situação", "classLimitDate":"Prazo da turma", "participationStatus":"Situação da participação", "classStatus":"Situação da turma", "complianceValidity":"Validade da conformidade", "nrId":"NR", "completionMode":"Forma de conclusão", "mandatoryEvidence":"Evidência obrigatória"}
            rows = [{"campo":labels[key],"valor":data.get(key)} for key in fields]
            if user.role != "COLABORADOR":
                # Safe class counts; never return participant arrays from the Java response.
                classes = data.get("eventClasses", [])
                rows.append({"campo":"Quantidade de turmas","valor":len(classes)})
                for item in classes:
                    rows.append({"campo":"Turma", "valor":f"{item.get('name', '—')} · {item.get('startDate', '—')} até {item.get('endDate', '—')}"})
            total = len(rows)
            rows = rows[(args.pagina-1)*args.limite:args.pagina*args.limite]
        else:
            if not isinstance(data,list):
                raise ValueError()
            rows = data
            if action == "consultar_conformidade_unidade":
                rows = [row for row in rows if (not args.unidade or row.get("unitName", "").casefold() == args.unidade.casefold()) and (not args.nr or row.get("nrId") == args.nr)]
            total = len(rows)
            rows = rows[(args.pagina-1)*args.limite:args.pagina*args.limite]
        if not all(isinstance(row,dict) for row in rows):
            raise ValueError()
        title = action.replace("consultar_", "").replace("_", " ").capitalize()
        if action == "consultar_respostas_formulario":
            title = "Preenchimento de uma instância de formulário (não é contagem de pessoas)"
        elif action == "consultar_candidatos_treinamento":
            title = "Candidatos elegíveis pela NR (não garante vaga ou inscrição)"
        return table(title, rows, columns, total, args.pagina, args.limite)
    except (KeyError, TypeError, ValueError):
        return {"status":"indisponivel","mensagem":"A API retornou dados incompatíveis com esta consulta."}


@tool("consultar_conformidade_unidade", args_schema=ApiArgs)
async def consultar_conformidade_unidade(config: RunnableConfig = None, **kwargs) -> dict:
    """Consulta registros autorizados de conformidade das unidades pela API Java."""
    return await execute_api("consultar_conformidade_unidade", ApiArgs.model_validate(kwargs), config)


@tool("consultar_formularios_pendentes", args_schema=ApiArgs)
async def consultar_formularios_pendentes(config: RunnableConfig = None, **kwargs) -> dict:
    """Consulta formulários pendentes recebidos pelo próprio gestor."""
    return await execute_api("consultar_formularios_pendentes", ApiArgs.model_validate(kwargs), config)


@tool("consultar_respostas_formulario", args_schema=ApiArgs)
async def consultar_respostas_formulario(config: RunnableConfig = None, **kwargs) -> dict:
    """Resume preenchimento de um formulário concluído autorizado, sem respostas sensíveis."""
    return await execute_api("consultar_respostas_formulario", ApiArgs.model_validate(kwargs), config)


@tool("consultar_detalhes_evento", args_schema=ApiArgs)
async def consultar_detalhes_evento(config: RunnableConfig = None, **kwargs) -> dict:
    """Detalha um evento autorizado; colaborador somente quando participante."""
    return await execute_api("consultar_detalhes_evento", ApiArgs.model_validate(kwargs), config)


@tool("consultar_candidatos_treinamento", args_schema=ApiArgs)
async def consultar_candidatos_treinamento(config: RunnableConfig = None, **kwargs) -> dict:
    """Lista candidatos calculados pela regra de elegibilidade da API Java para uma NR."""
    return await execute_api("consultar_candidatos_treinamento", ApiArgs.model_validate(kwargs), config)


TOOLS = [consultar_grafico_pendencias_nr, consultar_grafico_vencimentos, consultar_indicadores_equipe, consultar_vencimentos_conformidades, consultar_pendencias_equipe, consultar_conformidade_unidade, consultar_formularios_pendentes, consultar_respostas_formulario, consultar_detalhes_evento, consultar_candidatos_treinamento]
REGISTRY = {item.name:item for item in TOOLS}
DOMAINS = {name:("rh" if name == "consultar_indicadores_equipe" else "agenda" if name in {"consultar_detalhes_evento","consultar_candidatos_treinamento"} else "sst") for name in REGISTRY}


def is_operational_request(message, last_route=""):
    import re
    import unicodedata
    text = "".join(c for c in unicodedata.normalize("NFKD", message.casefold()) if not unicodedata.combining(c))
    if re.search(r"\bformularios?\b", text) and re.search(r"\b(pendentes?|respostas?|responder|responderam|preenchimento)\b", text):
        return True
    if re.search(r"\b(pendencias?|vencimentos?|vencidas?|prazos?)\b", text) and re.search(r"\b(graficos?|barras|setores|pizza|faixas|distribuicao|quantidade)\b", text):
        return True
    if "conformidade" in text and re.search(r"\bunidade\b|\bvenc", text):
        return True
    if re.search(r"\b(vencem|vencimentos)\b", text) and re.search(r"\b(nrs?|conformidades?)\b", text):
        return True
    if re.search(r"\bquem\b", text) and re.search(r"\b(realizar|renovar|pendentes?|participar)\b", text) and re.search(r"\b(nr|treinamentos?|equipe)\b", text):
        return True
    if re.search(r"\b(candidatos?|elegiveis)\b", text) and re.search(r"\b(treinamentos?|eventos?)\b", text):
        return True
    if re.search(r"\b(ficha|detalhes|turmas)\b", text) and re.search(r"\b(treinamentos?|eventos?)\b", text):
        return True
    return bool(re.search(r"\b(graficos?|distribuicao|indicadores)\b", text) and (re.search(r"\b(equipe|funcionarios?|usuarios?|colaboradores|cargos?|perfis)\b", text) or (last_route == "rh" and re.search(r"\b(nesses?|nessas?|desses?|dessas?|nisso|esses? dados|essas? pessoas)\b", text) and not re.search(r"\b(treinamentos?|eventos?)\b", text))))

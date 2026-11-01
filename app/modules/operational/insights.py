"""Consultas de gestão somente leitura; autorização é sempre aplicada no backend."""
import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Literal

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core import config as app_config
from app.infrastructure.database.connections import read_only_connection
from app.infrastructure.astro_queries import read_api


class InsightArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    cargo: str | None = Field(default=None, min_length=1, max_length=120)
    unidade: str | None = Field(default=None, min_length=1, max_length=200)
    nr: int | None = Field(default=None, ge=1, le=100)
    evento_id: int | None = Field(default=None, ge=1)
    conformidade_id: int | None = Field(default=None, ge=1)
    inicio: date | None = None
    fim: date | None = None
    escopo: Literal["proprio", "equipe"] = "proprio"
    incluir_agendados: bool = False
    pagina: int = Field(default=1, ge=1, le=1, description="Legado: somente 1; navegação visual local.")
    limite: int = Field(default=100, ge=1, le=1000000, description="Quantidade solicitada; coleta limitada a 200 registros e orçamento de resposta.")
    tipo_grafico: Literal["grafico_barras", "grafico_setores", "grafico_linhas"] = "grafico_barras"

    @model_validator(mode="after")
    def validar_periodo(self):
        if (self.inicio is None) != (self.fim is None):
            raise ValueError("Informe início e fim juntos.")
        if self.inicio and (self.fim < self.inicio or (self.fim-self.inicio).days > 366):
            raise ValueError("Período deve ser ordenado e ter até 366 dias.")
        return self


class EvolutionInsightArgs(InsightArgs):
    tipo_grafico: Literal["grafico_barras", "grafico_linhas"] = "grafico_linhas"


ACTIONS = {
    "consultar_matriz_conformidade_equipe", "consultar_cobertura_treinamentos",
    "consultar_evidencias_pendentes_validacao", "consultar_resultados_treinamento",
    "consultar_evolucao_conclusoes_equipe", "consultar_conflitos_agenda",
    "simular_impacto_exigencia_nr", "consultar_origem_conformidade",
    "consultar_indicadores_cobertura", "comparar_conformidade_unidades",
}


def clarify(message):
    return {"status":"esclarecer", "mensagem":message}


def literal(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def people_scope(args, user, *, own=False, exact_cargo=False):
    from app.modules.operational.tools import SCOPE
    if own:
        sql = """FROM usuario target
 JOIN unidade unit ON unit.id_unidade=target.unidade_id
 LEFT JOIN cargo ON cargo.id_cargo=target.cargo_id
 WHERE target.firebase_uid=%s AND target.tipo=%s AND target.status='ATIVO'"""
        params = [user.uid, user.role]
    else:
        sql = SCOPE + " AND target.status='ATIVO'"
        types = ["GESTOR", "COLABORADOR"] + (["GESTOR_WORKSPACE"] if user.role == "GESTOR_WORKSPACE" else [])
        params = [user.uid, user.role, user.uid, types]
    for column, value in (("target.nome",args.nome),("cargo.nome",args.cargo)):
        if value:
            if column == "cargo.nome" and exact_cargo:
                sql += " AND LOWER(cargo.nome)=LOWER(%s)"
                params.append(value)
            else:
                sql += " AND " + column + " ILIKE %s ESCAPE E'\\\\'"
                params.append(literal(value))
    if args.unidade:
        sql += " AND LOWER(unit.nome)=LOWER(%s)"
        params.append(args.unidade)
    return sql, params


REQUIREMENTS = """,
requirements AS (
 SELECT p.*, n.codigo_nr AS nr FROM people p
 JOIN cargo_nr cn ON cn.cargo_id=p.cargo_id
 JOIN unidade_nr un ON un.unidade_id=p.unidade_id AND un.nr_id=cn.nr_id
 JOIN nr_catalogo n ON n.codigo_nr=cn.nr_id AND n.revogada=FALSE
 WHERE (%s::integer IS NULL OR n.codigo_nr=%s)
), facts AS (
 SELECT r.*, c.id_conformidade AS conformidade_id, c.data_validade,
 EXISTS(SELECT 1 FROM turma_colaborador tc JOIN turma t ON t.id_turma=tc.turma_id
        JOIN evento e ON e.id_evento=t.evento_id
        LEFT JOIN conclusao_evento ce ON ce.turma_colaborador_id=tc.id_turma_colaborador
        WHERE tc.usuario_id=r.id_usuario AND e.nr_id=r.nr AND e.status='ATIVO'
          AND t.data_termino>=CURRENT_TIMESTAMP
          AND (ce.id_conclusao_evento IS NULL OR ce.status='REJEITADO')) AS agendado,
 EXISTS(SELECT 1 FROM turma_colaborador tc JOIN turma t ON t.id_turma=tc.turma_id
        JOIN evento e ON e.id_evento=t.evento_id
        JOIN conclusao_evento ce ON ce.turma_colaborador_id=tc.id_turma_colaborador
        WHERE tc.usuario_id=r.id_usuario AND e.nr_id=r.nr AND e.status='ATIVO'
          AND ce.status='PENDENTE') AS em_analise,
 CASE WHEN c.id_conformidade IS NULL THEN 'SEM_REGISTRO'
      WHEN c.data_validade IS NULL THEN 'SEM_VALIDADE'
      WHEN c.data_validade<CURRENT_DATE THEN 'VENCIDA' ELSE 'VIGENTE' END AS situacao
 FROM requirements r LEFT JOIN LATERAL (
 SELECT id_conformidade,data_validade FROM conformidade
 WHERE usuario_id=r.id_usuario AND nr_id=r.nr AND aplicavel IS TRUE
 ORDER BY data_validade DESC NULLS LAST,id_conformidade DESC LIMIT 1) c ON TRUE
)
"""


def build_query(action, args, user):
    own = action in {"consultar_conflitos_agenda", "consultar_origem_conformidade"} and (user.role == "COLABORADOR" or args.escopo == "proprio")
    scope, params = people_scope(args, user, own=own, exact_cargo=action=="simular_impacto_exigencia_nr")
    people = "WITH people AS (SELECT target.id_usuario,target.nome,target.cargo_id,target.unidade_id, unit.nome AS unidade " + scope + ")"
    warning = "Consulta cadastral; não certifica conformidade legal nem aptidão."
    graph = False
    if action in {"consultar_matriz_conformidade_equipe", "consultar_cobertura_treinamentos", "consultar_indicadores_cobertura", "comparar_conformidade_unidades"}:
        prefix = people + REQUIREMENTS
        params += [args.nr,args.nr]
        columns = [("referencia_pessoa","Referência da pessoa"),("nome","Pessoa"),("unidade","Unidade"),("nr","NR"),("situacao","Registro"),
                   ("conformidade_id","Referência da conformidade"),("validade","Validade"),("agendado","Atividade agendada"),("em_analise","Em análise")]
        select = "SELECT id_usuario AS referencia_pessoa,nome,unidade,nr,situacao,conformidade_id,data_validade AS validade,agendado,em_analise FROM facts"
        if action == "consultar_cobertura_treinamentos":
            select += " WHERE situacao<>'VIGENTE' AND (%s OR NOT (agendado OR em_analise))"
            params.append(args.incluir_agendados)
            warning += " Sem registro e sem validade são lacunas distintas. Por padrão exclui agendados/em análise; não inscreve pessoas."
        else:
            warning += " Matriz em formato pessoa/NR; NRs não aplicáveis não geram linha."
        title = "Cobertura de treinamentos por pessoa/NR" if action == "consultar_cobertura_treinamentos" else "Matriz cadastral de conformidade por pessoa/NR"
        order = "nome,referencia_pessoa,nr"
        if action=="consultar_indicadores_cobertura":
            select="""SELECT nr,COUNT(*) AS aplicaveis,
 COUNT(*) FILTER(WHERE situacao='VIGENTE') AS vigentes,
 COUNT(*) FILTER(WHERE situacao<>'VIGENTE' AND em_analise) AS em_analise,
 COUNT(*) FILTER(WHERE situacao<>'VIGENTE' AND NOT em_analise AND agendado) AS agendados,
 COUNT(*) FILTER(WHERE situacao<>'VIGENTE' AND NOT em_analise AND NOT agendado) AS sem_encaminhamento
 FROM facts GROUP BY nr"""
            columns=[("nr","NR"),("aplicaveis","Pares pessoa/NR"),("vigentes","Vigentes"),
                     ("em_analise","Em análise"),("agendados","Agendados"),("sem_encaminhamento","Sem encaminhamento")]
            title,order="Cobertura cadastral por NR","nr"
            warning="As quatro etapas são exclusivas: vigente, em análise, agendado, sem encaminhamento. O gráfico conta pares pessoa/NR sem encaminhamento, não pessoas únicas globais."
            graph=True
        elif action=="comparar_conformidade_unidades":
            select="""SELECT p.unidade_id,p.unidade,
 LEFT(COALESCE(p.unidade,''),70) || ' (#' || p.unidade_id::text || ')' AS categoria,
 COUNT(f.nr) AS aplicaveis,COUNT(f.nr) FILTER(WHERE f.situacao='VIGENTE') AS vigentes,
 ROUND(100.0 * COUNT(f.nr) FILTER(WHERE f.situacao='VIGENTE') / NULLIF(COUNT(f.nr),0),2)::float8 AS percentual,
 COUNT(DISTINCT p.id_usuario) AS pessoas
 FROM people p LEFT JOIN facts f ON f.id_usuario=p.id_usuario
 GROUP BY p.unidade_id,p.unidade"""
            columns=[("unidade_id","Referência da unidade"),("unidade","Unidade"),("pessoas","Pessoas"),
                     ("aplicaveis","Pares exigidos"),("vigentes","Pares vigentes"),("percentual","Vigentes / exigidos (%)")]
            title,order="Comparação cadastral entre unidades autorizadas","unidade,unidade_id"
            warning="Percentual = pares pessoa/NR vigentes / pares exigidos da unidade; denominador zero retorna nulo. O gráfico mostra contagens vigentes, não percentuais. Denominadores variam entre unidades."
            graph=True
    elif action == "simular_impacto_exigencia_nr":
        params += [args.nr,args.nr]
        prefix = people + """, facts AS (
 SELECT p.*, c.data_validade,
 CASE WHEN c.id_conformidade IS NULL THEN 'SEM_REGISTRO'
 WHEN c.data_validade IS NULL THEN 'SEM_VALIDADE'
 WHEN c.data_validade<CURRENT_DATE THEN 'VENCIDA' ELSE 'VIGENTE' END AS categoria,
 EXISTS(SELECT 1 FROM cargo_nr cn JOIN unidade_nr un ON un.nr_id=cn.nr_id
 WHERE cn.cargo_id=p.cargo_id AND un.unidade_id=p.unidade_id AND cn.nr_id=%s) AS ja_aplicavel
 FROM people p JOIN nr_catalogo n ON n.codigo_nr=%s AND n.revogada=FALSE
 LEFT JOIN LATERAL (SELECT id_conformidade,data_validade FROM conformidade
 WHERE usuario_id=p.id_usuario AND nr_id=n.codigo_nr AND aplicavel IS TRUE
 ORDER BY data_validade DESC NULLS LAST,id_conformidade DESC LIMIT 1) c ON TRUE)
"""
        select = "SELECT categoria,COUNT(*) AS quantidade,COUNT(*) FILTER(WHERE ja_aplicavel) AS ja_aplicavel FROM facts GROUP BY categoria"
        columns = [("categoria","Situação atual"),("quantidade","Pessoas"),("ja_aplicavel","Já exigida por cargo/unidade")]
        title, order = "Simulação cadastral de exigência de NR (sem alteração)", "categoria"
        warning += " Total da tabela representa categorias. Hipótese sobre o cargo/unidade selecionado; usa apenas registros já marcados aplicáveis. Não atribui NR nem prevê aptidão."
        graph = True
    elif action == "consultar_evolucao_conclusoes_equipe":
        prefix = people
        select = """SELECT TO_CHAR(DATE_TRUNC('month',ce.data_conclusao),'YYYY-MM') AS categoria,
 COUNT(*) AS quantidade FROM people p JOIN turma_colaborador tc ON tc.usuario_id=p.id_usuario
 JOIN turma t ON t.id_turma=tc.turma_id JOIN evento e ON e.id_evento=t.evento_id
 JOIN usuario owner ON owner.id_usuario=e.gestor_id JOIN unidade owner_unit ON owner_unit.id_unidade=owner.unidade_id
 JOIN conclusao_evento ce ON ce.turma_colaborador_id=tc.id_turma_colaborador
 WHERE ce.status='CONCLUIDO' AND ce.data_conclusao IS NOT NULL
 AND owner_unit.workspace_id=(SELECT u.workspace_id FROM usuario v JOIN unidade u ON u.id_unidade=v.unidade_id WHERE v.firebase_uid=%s)
 AND ce.data_conclusao::date BETWEEN COALESCE(%s::date,(DATE_TRUNC('month',CURRENT_DATE)-INTERVAL '11 months')::date) AND COALESCE(%s::date,CURRENT_DATE)
 AND (%s::integer IS NULL OR e.nr_id=%s)
 GROUP BY DATE_TRUNC('month',ce.data_conclusao)"""
        params += [user.uid,args.inicio,args.fim,args.nr,args.nr]
        title,order="Evolução das conclusões registradas da equipe", "categoria"
        columns=[("categoria","Mês"),("quantidade","Conclusões")]
        graph=True
        warning="Conta conclusões, não pessoas únicas, presença ou conformidade histórica. Total da tabela representa meses. Meses sem registros são omitidos."
    elif action == "consultar_conflitos_agenda":
        prefix = people + """, schedules AS (
 SELECT p.id_usuario,p.nome,tc.id_turma_colaborador,t.id_turma,e.id_evento,e.titulo,
 t.data_inicial,t.data_termino FROM people p JOIN turma_colaborador tc ON tc.usuario_id=p.id_usuario
 JOIN turma t ON t.id_turma=tc.turma_id JOIN evento e ON e.id_evento=t.evento_id
 JOIN usuario owner ON owner.id_usuario=e.gestor_id JOIN unidade u ON u.id_unidade=owner.unidade_id
 WHERE e.status='ATIVO' AND t.data_inicial<t.data_termino
 AND u.workspace_id=(SELECT un.workspace_id FROM usuario v JOIN unidade un ON un.id_unidade=v.unidade_id WHERE v.firebase_uid=%s)
 AND t.data_termino>=COALESCE(%s::date,CURRENT_DATE)
 AND t.data_inicial<COALESCE(%s::date+1,CURRENT_DATE+31))
"""
        params += [user.uid,args.inicio,args.fim]
        select="""SELECT a.id_usuario,a.nome,a.id_turma AS turma_a,b.id_turma AS turma_b,
 a.titulo AS evento_a,b.titulo AS evento_b,
 GREATEST(a.data_inicial,b.data_inicial) AS inicio,LEAST(a.data_termino,b.data_termino) AS fim
 FROM schedules a JOIN schedules b ON a.id_usuario=b.id_usuario AND a.id_turma<b.id_turma
 AND a.data_inicial<b.data_termino AND b.data_inicial<a.data_termino"""
        columns=[("nome","Pessoa"),("evento_a","Evento A"),("evento_b","Evento B"),("inicio","Início do conflito"),("fim","Fim do conflito")]
        title,order="Sobreposições entre turmas atribuídas", "inicio,id_usuario,turma_a,turma_b"
        warning="Sobreposição de horários cadastrados; não comprova presença nem cancela inscrições. Sem período: hoje até os próximos 30 dias."
    elif action == "consultar_evidencias_pendentes_validacao":
        prefix="WITH viewer AS (SELECT v.id_usuario,u.workspace_id FROM usuario v JOIN unidade u ON u.id_unidade=v.unidade_id WHERE v.firebase_uid=%s AND v.tipo=%s AND v.status='ATIVO')"
        params=[user.uid,user.role]
        select="""SELECT ce.id_conclusao_evento AS referencia,p.id_usuario AS usuario_id,e.id_evento AS evento_id,e.titulo AS evento,
 p.nome, t.nome AS turma, ce.data_conclusao AS envio,
 (SELECT COUNT(*) FROM evidencia ev WHERE ev.conclusao_evento_id=ce.id_conclusao_evento) AS arquivos
 FROM viewer v JOIN evento e ON e.gestor_id=v.id_usuario
 JOIN turma t ON t.evento_id=e.id_evento JOIN turma_colaborador tc ON tc.turma_id=t.id_turma
 JOIN usuario p ON p.id_usuario=tc.usuario_id JOIN unidade u ON u.id_unidade=p.unidade_id AND u.workspace_id=v.workspace_id
 JOIN conclusao_evento ce ON ce.turma_colaborador_id=tc.id_turma_colaborador
 WHERE e.status='ATIVO' AND e.modo_conclusao='COLABORADOR' AND ce.status='PENDENTE'
 AND (%s::bigint IS NULL OR e.id_evento=%s)"""
        params += [args.evento_id,args.evento_id]
        columns=[("referencia","Referência"),("evento_id","Evento"),("evento","Título"),("nome","Pessoa"),("turma","Turma"),("envio","Data registrada"),("arquivos","Arquivos")]
        title,order="Conclusões aguardando sua validação", "envio NULLS LAST,referencia"
        warning="Somente eventos ativos criados por você, modo colaborador, com conclusão enviada PENDENTE. Inclui envios sem arquivo; não abre arquivos, aprova ou rejeita."
    elif action == "consultar_origem_conformidade":
        prefix=people
        select="""SELECT c.id_conformidade AS referencia,p.nome,c.nr_id AS nr,c.origem,c.aplicavel,c.data_validade AS validade,
 c.conclusao_evento_id AS conclusao,c.formulario_resposta_id AS formulario
 FROM people p JOIN conformidade c ON c.usuario_id=p.id_usuario
 WHERE c.id_conformidade=%s AND (%s::integer IS NULL OR c.nr_id=%s)"""
        params += [args.conformidade_id,args.nr,args.nr]
        columns=[("referencia","Referência"),("nome","Pessoa"),("nr","NR"),("origem","Origem registrada"),("aplicavel","Aplicável"),("validade","Validade"),("conclusao","Conclusão de evento"),("formulario","Resposta de formulário")]
        title,order="Origem registrada da conformidade", "referencia"
        warning="Referências não concedem acesso ao evento, formulário ou arquivo; não são provas de verificação do documento. Conformidades de unidade não estão incluídas."
    else:
        raise ValueError("Consulta não registrada")
    # Count and page share one snapshot; even an empty page retains its total.
    query=prefix+", result AS ("+select+") "
    count=query+"SELECT COUNT(*) FROM result"
    listing=query+"SELECT "+",".join(k for k,_ in columns)+" FROM result ORDER BY "+order+" LIMIT %s OFFSET %s"
    return count,listing,params,columns,title,warning,graph


def sql_read(action,args,user):
    from app.modules.operational.visual_data import bounded_rows, finish, stats_query, items_from_stats, graph_query
    if not app_config.DATABASE_URL:
        return {"status":"indisponivel","mensagem":"Consulta ao cadastro indisponível."}
    try:
        count,query,params,columns,title,warning,graph=build_query(action,args,user)
        graph_data=None
        with read_only_connection(app_config.DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY", [])
                cur.execute(count.replace("SELECT COUNT(*) FROM result", "SELECT COUNT(*),CURRENT_DATE FROM result"),params)
                counted=cur.fetchone()
                total,reference=counted[0],counted[1] if len(counted)>1 else None
                cur.execute(stats_query(action,count),params)
                items=items_from_stats(action,cur.fetchone())
                rows,reason=bounded_rows(cur,query,params,columns,args,total)
                if graph:
                    cur.execute(graph_query(action,count),params)
                    groups=cur.fetchall()
                    if 0<len(groups)<=12 and len(groups)==total:
                        graph_data={"grafico":[{"categoria":row[0],"valor":row[1]} for row in groups],
                                    "tipo_grafico":args.tipo_grafico,
                                    "unidade_grafico":"participações" if action=="consultar_evolucao_conclusoes_equipe" else "pessoas"}
                        if action=="consultar_indicadores_cobertura":
                            graph_data.update(unidade_grafico="pendências",titulo_grafico="Pares pessoa/NR sem encaminhamento por NR")
                        elif action=="comparar_conformidade_unidades":
                            graph_data.update(unidade_grafico="registros",titulo_grafico="Pares pessoa/NR vigentes por unidade")
                    elif total:
                        warning+=" Gráfico omitido: exige todas as categorias e no máximo 12."
        return finish(action,args,user,rows,columns,total,title,warning,items,reason,reference,graph_data)
    except Exception as error:
        from app.diagnostics import diagnostic
        logging.getLogger(__name__).warning("Consulta analitica falhou acao=%s",action,extra=diagnostic(error))
        return {"status":"indisponivel","mensagem":"Não foi possível consultar o cadastro. Nenhum resultado foi confirmado."}


async def event_results(args,user):
    from app.modules.operational.visual_data import finish,count_item,percentage_item,MAX_RESULTS
    response=await read_api(f"/events/{args.evento_id}/participants", {})
    if response.get("status")!="ok":
        return response
    try:
        records=response["data"]
        if not isinstance(records,list):
            raise ValueError()
        statuses={key:{"count":0,"people":set()} for key in ("PENDENTE","EM_ANALISE","CONCLUIDO","REJEITADO")}
        people=set()
        for item in records:
            if not isinstance(item,dict) or type(item.get("participantId")) is not int or item["participantId"]<=0 or item.get("participationStatus") not in statuses:
                raise ValueError()
            group=statuses[item["participationStatus"]]
            group["count"]+=1
            group["people"].add(item["participantId"])
            people.add(item["participantId"])
        all_rows=[{"categoria":key,"quantidade":value["count"],"pessoas_unicas":len(value["people"])} for key,value in statuses.items()] if records else []
        rows=all_rows[:min(args.limite,MAX_RESULTS)]
        columns=[("categoria","Situação"),("quantidade","Participações"),("pessoas_unicas","Pessoas únicas nesta situação")]
        n=len(records)
        criterion="Participações retornadas pela API Java no evento autorizado; não comprova presença."
        items=[count_item("participacoes","Participações",n,"participações",criterion),
               count_item("pessoas","Pessoas únicas",len(people),"pessoas","Pessoas distintas em todas as situações; não soma as contagens por situação."),
               *[count_item(key.lower(),label,statuses[key]["count"],"participações",criterion) for key,label in
                 (("CONCLUIDO","Concluídas"),("PENDENTE","Ainda não enviadas"),("EM_ANALISE","Em análise"),("REJEITADO","Rejeitadas"))],
               percentage_item("percentual_concluidas","Conclusões registradas",statuses["CONCLUIDO"]["count"],n,"Participações concluídas / participações registradas; não é frequência.")]
        warning="Total da tabela representa situações. Conta participações, não presença. Pessoa em várias turmas pode aparecer mais de uma vez; pessoas únicas não se somam entre situações."
        graph={"grafico":[{"categoria":r["categoria"],"valor":r["quantidade"]} for r in all_rows],"tipo_grafico":args.tipo_grafico,"unidade_grafico":"participações"} if records else None
        return finish("consultar_resultados_treinamento",args,user,rows,columns,len(all_rows),"Resultados registrados do treinamento",warning,items,None,datetime.now(timezone.utc).date(),graph,
                      source="API Java Astro: participações autorizadas do evento")
    except (KeyError,TypeError,ValueError):
        return {"status":"indisponivel","mensagem":"A API retornou dados incompatíveis com esta consulta."}


async def execute(action,args,config):
    from app.modules.operational.tools import user_from, denied
    user=user_from(config)
    own_allowed=action in {"consultar_conflitos_agenda","consultar_origem_conformidade"}
    if user is None or user.role not in ({"GESTOR","GESTOR_WORKSPACE","COLABORADOR"} if own_allowed else {"GESTOR","GESTOR_WORKSPACE"}):
        return denied()
    if action=="simular_impacto_exigencia_nr":
        if user.role!="GESTOR_WORKSPACE":
            return denied()
        if args.nr is None or not (args.cargo or args.unidade):
            return clarify("Informe a NR e o cargo ou unidade para simular a hipótese, sem alterar o cadastro.")
    if action=="consultar_resultados_treinamento":
        if args.evento_id is None:
            return clarify("Selecione a referência do evento desejado; não vou adivinhar o treinamento.")
        defaults=InsightArgs().model_dump()
        if any(value!=defaults[key] and key not in {"evento_id","pagina","limite","tipo_grafico"} for key,value in args.model_dump().items()):
            return clarify("Resultados do evento não aceitam filtros de equipe; selecione o evento autorizado.")
        if args.tipo_grafico=="grafico_linhas":
            return clarify("Resultados por situação aceitam barras ou setores; evolução mensal usa a consulta de conclusões.")
        return await event_results(args,user)
    if action=="consultar_origem_conformidade" and args.conformidade_id is None:
        return clarify("Informe a referência da conformidade individual; ela será verificada no seu escopo.")
    # Reject filters which this query cannot apply instead of silently broadening.
    supported={
        "consultar_matriz_conformidade_equipe":{"nome","cargo","unidade","nr"},
        "consultar_indicadores_cobertura":{"nome","cargo","unidade","nr","tipo_grafico"},
        "comparar_conformidade_unidades":{"nome","cargo","unidade","nr","tipo_grafico"},
        "consultar_cobertura_treinamentos":{"nome","cargo","unidade","nr","incluir_agendados"},
        "simular_impacto_exigencia_nr":{"cargo","unidade","nr","tipo_grafico"},
        "consultar_evolucao_conclusoes_equipe":{"nome","cargo","unidade","nr","inicio","fim","tipo_grafico"},
        "consultar_conflitos_agenda":{"escopo","nome","cargo","unidade","inicio","fim"},
        "consultar_evidencias_pendentes_validacao":{"evento_id"},
        "consultar_origem_conformidade":{"escopo","nome","cargo","unidade","nr","conformidade_id"},
    }[action]
    defaults=InsightArgs().model_dump()
    if any(value!=defaults[key] and key not in supported|{"pagina","limite"} for key,value in args.model_dump().items()):
        return clarify("Há filtros não suportados por esta consulta. Informe apenas os critérios documentados.")
    if user.role=="COLABORADOR" and (args.escopo!="proprio" or any((args.nome,args.cargo,args.unidade))):
        return clarify("Esta consulta usa somente seu cadastro autenticado; não selecione outra pessoa.")
    if action=="consultar_evolucao_conclusoes_equipe" and args.tipo_grafico=="grafico_setores":
        return clarify("Para comparar meses, use linhas ou barras; setores não representam esta evolução.")
    if action in {"simular_impacto_exigencia_nr","consultar_indicadores_cobertura","comparar_conformidade_unidades"} and args.tipo_grafico=="grafico_linhas":
        return clarify("Esta consulta cadastral não possui série histórica; use barras ou setores.")
    return await asyncio.to_thread(sql_read,action,args,user)


@tool("consultar_matriz_conformidade_equipe",args_schema=InsightArgs)
async def consultar_matriz_conformidade_equipe(config: RunnableConfig = None,**kwargs):
    """Lista pessoa/NR aplicável com validade, lacuna, agendamento e análise."""
    return await execute("consultar_matriz_conformidade_equipe",InsightArgs.model_validate(kwargs),config)


@tool("consultar_cobertura_treinamentos",args_schema=InsightArgs)
async def consultar_cobertura_treinamentos(config: RunnableConfig = None,**kwargs):
    """Cruza necessidade cadastral com inscrições, distinguindo envios em análise."""
    return await execute("consultar_cobertura_treinamentos",InsightArgs.model_validate(kwargs),config)


@tool("consultar_evidencias_pendentes_validacao",args_schema=InsightArgs)
async def consultar_evidencias_pendentes_validacao(config: RunnableConfig = None,**kwargs):
    """Lista conclusões pendentes somente de eventos criados pelo gestor autenticado."""
    return await execute("consultar_evidencias_pendentes_validacao",InsightArgs.model_validate(kwargs),config)


@tool("consultar_resultados_treinamento",args_schema=InsightArgs)
async def consultar_resultados_treinamento(config: RunnableConfig = None,**kwargs):
    """Agrega participações por situação usando a autorização da API Java."""
    return await execute("consultar_resultados_treinamento",InsightArgs.model_validate(kwargs),config)


@tool("consultar_evolucao_conclusoes_equipe",args_schema=EvolutionInsightArgs)
async def consultar_evolucao_conclusoes_equipe(config: RunnableConfig = None,**kwargs):
    """Conta conclusões registradas por mês na equipe autorizada."""
    return await execute("consultar_evolucao_conclusoes_equipe",InsightArgs.model_validate({"tipo_grafico":"grafico_linhas",**kwargs}),config)


@tool("consultar_conflitos_agenda",args_schema=InsightArgs)
async def consultar_conflitos_agenda(config: RunnableConfig = None,**kwargs):
    """Encontra pares de turmas atribuídas com intervalos sobrepostos."""
    return await execute("consultar_conflitos_agenda",InsightArgs.model_validate(kwargs),config)


@tool("simular_impacto_exigencia_nr",args_schema=InsightArgs)
async def simular_impacto_exigencia_nr(config: RunnableConfig = None,**kwargs):
    """Simula hipótese de exigência de NR sem alterar vínculos, inscrições ou registros."""
    return await execute("simular_impacto_exigencia_nr",InsightArgs.model_validate(kwargs),config)


@tool("consultar_origem_conformidade",args_schema=InsightArgs)
async def consultar_origem_conformidade(config: RunnableConfig = None,**kwargs):
    """Retorna referências de origem da conformidade sem abrir formulário ou evidência."""
    return await execute("consultar_origem_conformidade",InsightArgs.model_validate(kwargs),config)


@tool("consultar_indicadores_cobertura",args_schema=InsightArgs)
async def consultar_indicadores_cobertura(config: RunnableConfig = None,**kwargs):
    """Retorna cartões de cobertura global e barras de lacunas sem encaminhamento por NR."""
    return await execute("consultar_indicadores_cobertura",InsightArgs.model_validate(kwargs),config)


@tool("comparar_conformidade_unidades",args_schema=InsightArgs)
async def comparar_conformidade_unidades(config: RunnableConfig = None,**kwargs):
    """Compara numeradores, denominadores e percentuais cadastrais entre unidades autorizadas."""
    return await execute("comparar_conformidade_unidades",InsightArgs.model_validate(kwargs),config)


TOOLS=[consultar_matriz_conformidade_equipe,consultar_cobertura_treinamentos,
       consultar_evidencias_pendentes_validacao,consultar_resultados_treinamento,
       consultar_evolucao_conclusoes_equipe,consultar_conflitos_agenda,
       simular_impacto_exigencia_nr,consultar_origem_conformidade,consultar_indicadores_cobertura,comparar_conformidade_unidades]


def is_insight_request(message):
    import re
    import unicodedata
    text="".join(c for c in unicodedata.normalize("NFKD",message.casefold()) if not unicodedata.combining(c))
    return bool(re.search(
        r"\b(resumo|cartoes|indicadores)\b.*\b(cobertura|conformidade|encaminhamento)\b|"
        r"\bresumo\b.*\bequipe\b|"
        r"\bresumo\b.*\b(treinamentos?|eventos?|turmas?)\b|"
        r"\b(compar\w*|percentual|conformidade)\b.*\bunidades\b|"
        r"\bunidades\b.*\b(compar\w*|percentual|conformidade)\b|"
        r"\b(matriz|cobertura)\b.*\b(conformidade|nrs?|treinamentos?|equipe)\b|"
        r"\b(quem|pessoas|funcionarios|colaboradores)\b.*\b(precisa|precisam|necessita|necessitam)\b.*\b(sem treinamento|nao esta|nao estao|nao tem|nao foram)\b|"
        r"\b(evidencias?|conclusoes)\b.*\b(validacao|validar|revisao|revisar)\b|"
        r"\b(resultados?|desempenho)\b.*\b(treinamentos?|eventos?|turmas?)\b|"
        r"\b(como foi|como foram)\b.*\b(treinamentos?|eventos?)\b|"
        r"\b(evolucao|mensal|meses|por mes)\b.*\b(conclusoes|equipe)\b|"
        r"\bconclusoes\b.*\b(mes|mensal|evolucao)\b|"
        r"\b(conflitos?|sobreposicoes?|sobrepostos?|mesmo horario)\b|"
        r"\b(impacto|simul\w*|se exigirmos)\b.*\b(nr|exigencia)\b|"
        r"\b(origem|de onde veio|rastrear)\b.*\bconformidade\b",text))

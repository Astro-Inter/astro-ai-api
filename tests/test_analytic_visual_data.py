import asyncio
from contextlib import contextmanager
from datetime import date
import json

import pytest
from pydantic import ValidationError
from app.core.security import CurrentUser
from app.modules.operational import insights, visual_data
from app.modules.chat.analytic_blocks import build_analytic_blocks
from app.modules.chat.schemas import (
    IndicadorVisual,BlocoIndicadores,BlocoMatrizConformidade,BlocoContextoDados,SessionMessage,
)


def user():
    return CurrentUser(uid="viewer",role="GESTOR_WORKSPACE")


def matrix_rows(n):
    return [{"referencia_pessoa":i+1,"nome":f"Pessoa {i+1}","unidade":"Unidade", "nr":10,
             "situacao":"VIGENTE","conformidade_id":i+1000,"validade":date(2027,1,1),
             "agendado":False,"em_analise":False} for i in range(n)]


class FakeCursor:
    def __init__(self,action,records,stats,graphs=None):
        self.action=action
        self.records=records
        self.stats=stats
        self.graphs=graphs or []
        self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,q,p):
        assert q.count("%s")==len(p)
        self.query,self.params=q,p
        self.calls.append((q,p))
    def fetchone(self):
        if "SELECT COUNT(*),CURRENT_DATE FROM result" in self.query:
            return (len(self.records),date(2026,10,31))
        return self.stats
    def fetchall(self):
        if self.query.endswith("LIMIT 13"):
            return self.graphs[:13]
        size,offset=self.params[-2:]
        assert 1 <= size <= 50
        columns=insights.build_query(self.action,insights.InsightArgs(),user())[3]
        return [tuple(row[key] for key,_ in columns) for row in self.records[offset:offset+size]]


def fake_db(monkeypatch,action,records,stats,graphs=None):
    cursor=FakeCursor(action,records,stats,graphs)
    class Conn:
        def cursor(self):return cursor
    @contextmanager
    def connection(*args):yield Conn()
    monkeypatch.setattr(insights.app_config,"DATABASE_URL","postgresql://fake")
    monkeypatch.setattr(insights,"read_only_connection",connection)
    return cursor


@pytest.mark.parametrize("requested,expected,limited",[(100,100,False),(500,200,True)])
def test_local_collection_batches_and_global_cards(monkeypatch,requested,expected,limited):
    name="consultar_matriz_conformidade_equipe"
    cursor=fake_db(monkeypatch,name,matrix_rows(273),(273,273,273,0,0,0,0))
    result=insights.sql_read(name,insights.InsightArgs(limite=requested),user())
    assert result["status"]=="ok"
    assert result["retornada"]==expected and result["total"]==273
    assert result["limitado"] is limited and result["tem_mais"]
    assert result["motivo_limite"]==("maximo_resultados" if limited else None)
    batches=[params[-2:] for q,params in cursor.calls if q.endswith("LIMIT %s OFFSET %s")]
    assert batches==[[50,offset] for offset in range(0,expected,50)]
    assert result["cartoes"]["indicadores"][1]["valor"]==273
    blocks=build_analytic_blocks(name,result)
    assert [b.tipo for b in blocks]==["indicadores","matriz_conformidade","contexto_dados"]
    assert len(blocks[1].linhas)==expected and blocks[1].paginacao=="local"
    message=SessionMessage(role="assistant",content="Resumo",blocos=blocks)
    assert SessionMessage.model_validate_json(message.model_dump_json())==message


def test_byte_budget_cut_is_reported_without_truncating_global_metrics(monkeypatch):
    name="consultar_matriz_conformidade_equipe"
    cursor=fake_db(monkeypatch,name,matrix_rows(3),(3,3,3,0,0,0,0))
    monkeypatch.setattr(visual_data,"MAX_TABLE_BYTES",17000)
    result=insights.sql_read(name,insights.InsightArgs(limite=3),user())
    assert result["status"]=="ok" and result["retornada"]<3
    assert result["motivo_limite"]=="tamanho_resposta" and result["limitado"]
    assert result["cartoes"]["indicadores"][1]["valor"]==3
    assert result["contexto_dados"]["conjunto"]["total"]==3


def test_graph_uses_all_months_when_table_requests_one_row(monkeypatch):
    name="consultar_evolucao_conclusoes_equipe"
    records=[{"categoria":f"2026-{i:02}","quantidade":i} for i in range(1,5)]
    fake_db(monkeypatch,name,records,(10,4),[(r["categoria"],r["quantidade"]) for r in records])
    result=insights.sql_read(name,insights.InsightArgs(limite=1,tipo_grafico="grafico_linhas"),user())
    assert result["retornada"]==1 and result["total"]==4
    assert sum(p["valor"] for p in result["grafico"])==10 and len(result["grafico"])==4
    assert result["cartoes"]["indicadores"][0]["valor"]==10
    assert result["contexto_dados"]["inicio"]=="2025-11-01"
    assert result["contexto_dados"]["fim"]=="2026-10-31"
    assert [b.tipo for b in build_analytic_blocks(name,result)]==["indicadores","tabela","grafico_linhas","contexto_dados"]


def test_too_many_categories_does_not_emit_misleading_graph(monkeypatch):
    name="consultar_evolucao_conclusoes_equipe"
    records=[{"categoria":f"2025-{i:02}","quantidade":1} for i in range(1,14)]
    fake_db(monkeypatch,name,records,(13,13),[(r["categoria"],1) for r in records])
    result=insights.sql_read(name,insights.InsightArgs(limite=1),user())
    assert "grafico" not in result
    assert "12" in result["aviso"]
    assert result["cartoes"]["indicadores"][0]["valor"]==13


def test_coverage_stages_are_disjoint_and_global(monkeypatch):
    name="consultar_indicadores_cobertura"
    records=[{"nr":10,"aplicaveis":8,"vigentes":4,"em_analise":1,"agendados":2,"sem_encaminhamento":1},
             {"nr":35,"aplicaveis":4,"vigentes":1,"em_analise":1,"agendados":1,"sem_encaminhamento":1}]
    fake_db(monkeypatch,name,records,(12,8,5,5,2,3,2),[("NR-10",1),("NR-35",1)])
    result=insights.sql_read(name,insights.InsightArgs(limite=1),user())
    assert result["retornada"]==1 and len(result["grafico"])==2
    values={i["chave"]:i["valor"] for i in result["cartoes"]["indicadores"]}
    assert values["vigentes"]+values["em_analise"]+values["agendados"]+values["sem_encaminhamento"]==values["pares_aplicaveis"]
    assert values["pessoas_com_exigencias"]==8
    assert result["unidade_grafico"]=="pendências"


def test_unit_comparison_keeps_zero_denominator_unknown(monkeypatch):
    name="comparar_conformidade_unidades"
    records=[{"unidade_id":1,"unidade":"Unidade","pessoas":3,"aplicaveis":0,"vigentes":0,"percentual":None}]
    fake_db(monkeypatch,name,records,(0,0,0,0,0,0,0),[("Unidade (#1)",0)])
    result=insights.sql_read(name,insights.InsightArgs(),user())
    assert result["status"]=="ok" and result["linhas"][0]["percentual"] is None
    assert result["cartoes"]["indicadores"][-1]["valor"] is None
    assert result["grafico"][0]["valor"]==0
    assert result["unidade_grafico"]=="registros"


def test_event_summary_is_independent_of_table_limit(monkeypatch):
    async def api(*a):
        return {"status":"ok","data":[{"participantId":1,"participationStatus":"CONCLUIDO"},
            {"participantId":1,"participationStatus":"CONCLUIDO"},{"participantId":2,"participationStatus":"EM_ANALISE"}]}
    monkeypatch.setattr(insights,"read_api",api)
    result=asyncio.run(insights.event_results(insights.InsightArgs(evento_id=1,limite=1),user()))
    assert result["retornada"]==1 and result["total"]==4 and len(result["grafico"])==4
    items={i["chave"]:i for i in result["cartoes"]["indicadores"]}
    assert items["pessoas"]["valor"]==2 and items["participacoes"]["valor"]==3
    assert items["percentual_concluidas"]["valor"]==66.67
    assert items["percentual_concluidas"]["denominador"]==3


def test_empty_collection_keeps_zero_cards_and_unknown_percentage(monkeypatch):
    name="consultar_matriz_conformidade_equipe"
    fake_db(monkeypatch,name,[],(0,0,0,0,0,0,0))
    result=insights.sql_read(name,insights.InsightArgs(),user())
    assert result["status"]=="sem_dados" and result["retornada"]==0
    assert result["cartoes"]["indicadores"][-1]["valor"] is None
    assert [b.tipo for b in build_analytic_blocks(name,result)]==["indicadores","contexto_dados"]


@pytest.mark.parametrize("value,numerator,denominator",[(0,0,0),(50,1,3),(100,2,1)])
def test_percentage_cards_reject_invented_values(value,numerator,denominator):
    with pytest.raises(ValidationError):
        IndicadorVisual(chave="percentual",rotulo="Percentual",valor=value,unidade="%",numerador=numerator,denominador=denominator,criterio="Teste")


def test_percentage_and_counts_contract():
    item=IndicadorVisual(chave="percentual",rotulo="Percentual",valor=None,unidade="%",numerador=0,denominador=0,criterio="Teste")
    assert item.valor is None
    with pytest.raises(ValidationError):
        IndicadorVisual(chave="contagem",rotulo="Contagem",valor=True,unidade="pessoas",criterio="Teste")


def test_matrix_rejects_duplicate_cells(monkeypatch):
    name="consultar_matriz_conformidade_equipe"
    fake_db(monkeypatch,name,matrix_rows(1),(1,1,1,0,0,0,0))
    data=insights.sql_read(name,insights.InsightArgs(),user())
    fields={key:data[key] for key in ("titulo","colunas","linhas","pagina","total_paginas","total","solicitada","retornada","limitado","tem_mais","motivo_limite","paginacao")}
    fields.update(linhas=fields["linhas"]*2,total=2,retornada=2)
    with pytest.raises(ValidationError):
        BlocoMatrizConformidade(**fields)


def test_metadata_does_not_accept_identity_or_credentials(monkeypatch):
    name="consultar_matriz_conformidade_equipe"
    fake_db(monkeypatch,name,[],(0,0,0,0,0,0,0))
    data=insights.sql_read(name,insights.InsightArgs(),user())
    fields=data["contexto_dados"]
    fields["filtros"]={"uid":"other"}
    with pytest.raises(ValidationError):
        BlocoContextoDados.model_validate(fields)


@pytest.mark.parametrize("question",[
    "Faça um resumo desse treinamento", "Mostre indicadores de cobertura da equipe",
    "Compare a conformidade entre unidades", "Mostre os cartões de conformidade",
])
def test_summary_requests_use_operational_insight_routing(question):
    assert insights.is_insight_request(question)


def test_direct_monthly_tool_defaults_to_lines(monkeypatch):
    captured=[]
    def sql(action,args,user):
        captured.append(args.tipo_grafico)
        return {"status":"sem_dados"}
    monkeypatch.setattr(insights,"sql_read",sql)
    asyncio.run(insights.consultar_evolucao_conclusoes_equipe.ainvoke({},config={"configurable":{"usuario_atual":user().model_dump()}}))
    assert captured==["grafico_linhas"]

"""Conjuntos locais limitados e indicadores calculados fora dos modelos de IA."""
import json
from datetime import date, timedelta

from app.modules.chat.schemas import BlocoContextoDados, BlocoIndicadores, ResumoConjunto

MAX_RESULTS = 200
QUERY_BATCH = 50
MAX_TABLE_BYTES = 1_500_000  # reserva para cartões, metadados, gráfico e envelope de 2 MiB


def count_item(key,label,value,unit,criterion):
    return {"chave":key,"rotulo":label,"valor":value,"unidade":unit,"criterio":criterion}


def percentage_item(key,label,numerator,denominator,criterion):
    return {"chave":key,"rotulo":label,"valor":round(100*numerator/denominator,2) if denominator else None,
            "unidade":"%","numerador":numerator,"denominador":denominator,"criterio":criterion}


def bounded_rows(cur,query,params,columns,args,total):
    from app.modules.operational.tools import scalar
    target=min(args.limite,MAX_RESULTS,total)
    rows=[]
    used=16384
    reason=None
    while len(rows)<target:
        size=min(QUERY_BATCH,target-len(rows))
        cur.execute(query,[*params,size,len(rows)])
        batch=cur.fetchall()
        if not batch or len(batch)>size:
            raise ValueError("Contagem e coleta inconsistentes no snapshot.")
        for values in batch:
            if len(values)!=len(columns):
                raise ValueError("Colunas inconsistentes.")
            row={key:scalar(value) for (key,_),value in zip(columns,values)}
            cost=len(json.dumps(row,ensure_ascii=True,allow_nan=False).encode("utf-8"))+128
            if used+cost>MAX_TABLE_BYTES:
                reason="tamanho_resposta"
                break
            used+=cost
            rows.append(row)
        if reason:
            break
    if reason is None and len(rows)<min(args.limite,total):
        reason="maximo_resultados"
    return rows,reason


def items_from_stats(action, values):
    if any(value < 0 or int(value) != value for value in values):
        raise ValueError("Contagem agregada inválida.")
    values=tuple(int(value) for value in values)
    if action in {"consultar_matriz_conformidade_equipe","consultar_cobertura_treinamentos",
                  "consultar_indicadores_cobertura","comparar_conformidade_unidades"}:
        pairs,people,valid,people_gaps,review,scheduled,uncovered=values
        criterion="Exigências do cadastro ativo autorizado, antes do filtro de encaminhamento da lista."
        return [count_item("pessoas_com_exigencias","Pessoas com exigências",people,"pessoas",criterion),
                count_item("pares_aplicaveis","Exigências pessoa/NR",pairs,"pares_pessoa_nr",criterion),
                count_item("vigentes","Pares vigentes",valid,"pares_pessoa_nr",criterion),
                count_item("pessoas_com_lacunas","Pessoas com lacunas",people_gaps,"pessoas",criterion),
                count_item("em_analise","Pares aguardando análise",review,"pares_pessoa_nr","Entre não vigentes: envio em análise tem prioridade sobre agendamento."),
                count_item("agendados","Pares com atividade agendada",scheduled,"pares_pessoa_nr","Entre não vigentes, sem envio em análise, com atividade futura cadastrada."),
                count_item("sem_encaminhamento","Pares sem encaminhamento",uncovered,"pares_pessoa_nr","Não vigentes, sem análise e sem atividade futura cadastrada."),
                percentage_item("percentual_vigentes","Vigência cadastral",valid,pairs,"Pares pessoa/NR vigentes / pares exigidos pelo cargo e unidade. Não certifica aptidão ou conformidade legal.")]
    if action=="simular_impacto_exigencia_nr":
        people,valid,required=values
        return [count_item("pessoas","Pessoas na hipótese",people,"pessoas","Equipe autorizada no cargo/unidade selecionados; exclui o solicitante."),
                count_item("ja_exigida","Pessoas com exigência atual",required,"pessoas","Interseção atual dos vínculos cargo/NR e unidade/NR."),
                percentage_item("percentual_vigentes","Registros vigentes na hipótese",valid,people,"Registros já aplicáveis e vigentes / pessoas da hipótese; não altera aplicabilidade.")]
    if action=="consultar_evolucao_conclusoes_equipe":
        total,months=values
        return [count_item("conclusoes","Conclusões registradas",total,"conclusões","Conclusões CONCLUIDO pela data_conclusao, em todo o período autorizado."),
                count_item("meses_com_registros","Meses com registros",months,"meses","Meses sem registros são omitidos, não interpretados como zeros.")]
    if action=="consultar_conflitos_agenda":
        total,people=values
        return [count_item("conflitos","Pares de turmas em conflito",total,"conflitos","Pares únicos com interseção estrita de horários."),
                count_item("pessoas","Pessoas envolvidas",people,"pessoas","Pessoas distintas no conjunto completo de conflitos.")]
    if action=="consultar_evidencias_pendentes_validacao":
        total,people,files=values
        return [count_item("envios","Envios aguardando validação",total,"envios","Conclusões PENDENTE de eventos ativos criados pelo solicitante."),
                count_item("pessoas","Pessoas com envio",people,"pessoas","Pessoas distintas; uma pessoa pode ter mais de um envio."),
                count_item("arquivos","Arquivos referenciados",files,"registros","Metadados de arquivos; não certifica conteúdo nem autoriza download.")]
    return [count_item("registros","Registros autorizados",values[0],"registros","Referências consultadas no escopo atual, sem abrir documentos.")]


def stats_query(action,count_query):
    prefix=count_query.rsplit("SELECT COUNT(*) FROM result",1)[0]
    if action in {"consultar_matriz_conformidade_equipe","consultar_cobertura_treinamentos",
                  "consultar_indicadores_cobertura","comparar_conformidade_unidades"}:
        return prefix+"""SELECT COUNT(*),COUNT(DISTINCT id_usuario),
 COUNT(*) FILTER(WHERE situacao='VIGENTE'),
 COUNT(DISTINCT id_usuario) FILTER(WHERE situacao<>'VIGENTE'),
 COUNT(*) FILTER(WHERE situacao<>'VIGENTE' AND em_analise),
 COUNT(*) FILTER(WHERE situacao<>'VIGENTE' AND NOT em_analise AND agendado),
 COUNT(*) FILTER(WHERE situacao<>'VIGENTE' AND NOT em_analise AND NOT agendado) FROM facts"""
    if action=="simular_impacto_exigencia_nr":
        return prefix+"SELECT COALESCE(SUM(quantidade),0),COALESCE(SUM(quantidade) FILTER(WHERE categoria='VIGENTE'),0),COALESCE(SUM(ja_aplicavel),0) FROM result"
    if action=="consultar_evolucao_conclusoes_equipe":
        return prefix+"SELECT COALESCE(SUM(quantidade),0),COUNT(*) FROM result"
    if action=="consultar_conflitos_agenda":
        return prefix+"SELECT COUNT(*),COUNT(DISTINCT id_usuario) FROM result"
    if action=="consultar_evidencias_pendentes_validacao":
        return prefix+"SELECT COUNT(*),COUNT(DISTINCT usuario_id),COALESCE(SUM(arquivos),0) FROM result"
    return prefix+"SELECT COUNT(*) FROM result"


def graph_query(action,count_query):
    prefix=count_query.rsplit("SELECT COUNT(*) FROM result",1)[0]
    if action=="consultar_indicadores_cobertura":
        return prefix+"SELECT 'NR-' || nr::text AS categoria, sem_encaminhamento AS quantidade FROM result ORDER BY nr LIMIT 13"
    if action=="comparar_conformidade_unidades":
        return prefix+"SELECT categoria,vigentes AS quantidade FROM result ORDER BY categoria,unidade_id LIMIT 13"
    return prefix+"SELECT categoria,quantidade FROM result ORDER BY categoria LIMIT 13"


def result_unit(action):
    return {"consultar_matriz_conformidade_equipe":"pares pessoa/NR", "consultar_cobertura_treinamentos":"pares pessoa/NR",
            "consultar_indicadores_cobertura":"NRs", "comparar_conformidade_unidades":"unidades",
            "consultar_evolucao_conclusoes_equipe":"meses", "consultar_evidencias_pendentes_validacao":"envios",
            "consultar_conflitos_agenda":"pares de turmas", "simular_impacto_exigencia_nr":"categorias",
            "consultar_resultados_treinamento":"situações"}.get(action,"registros")


def finish(action,args,user,rows,columns,total,title,warning,items,reason,reference,graph=None,*,source="Consulta autorizada ao cadastro Astro"):
    from app.modules.operational.tools import table
    if action=="comparar_conformidade_unidades":
        for row in rows:
            numerator,denominator=row["vigentes"],row["aplicaveis"]
            if type(numerator) is not int or type(denominator) is not int or not 0 <= numerator <= denominator:
                raise ValueError("Contagens de unidade inconsistentes.")
            row["percentual"]=round(100*numerator/denominator,2) if denominator else None
    result=table(title,rows,columns,total,1,max(1,total))
    result["fonte"]=source
    result.update(status="ok" if total else "sem_dados",paginacao="local",pagina=1,total_paginas=1,
                  solicitada=args.limite,retornada=len(rows),quantidade=len(rows),
                  limitado=len(rows)<min(total,args.limite),tem_mais=len(rows)<total,motivo_limite=reason,
                  completo=len(rows)==total,aviso=warning)
    if not rows:
        result["mensagem"]=f"Retornados 0 de {total} {result_unit(action)} autorizados."
    if graph:
        result.update(graph)
    filters=args.model_dump(mode="json",exclude_none=True,exclude={"pagina","limite","tipo_grafico","inicio","fim","escopo"})
    if action!="consultar_cobertura_treinamentos":
        filters.pop("incluir_agendados",None)
    if action not in {"consultar_resultados_treinamento","consultar_evidencias_pendentes_validacao"}:
        filters["status"]=["ATIVO"]
    own=action in {"consultar_conflitos_agenda","consultar_origem_conformidade"} and (user.role=="COLABORADOR" or args.escopo=="proprio")
    scope="Próprio cadastro autenticado" if own else "Equipe nas unidades autorizadas do solicitante"
    if action=="consultar_resultados_treinamento":
        scope="Evento autorizado pela API Java no workspace do solicitante"
    if action=="consultar_evidencias_pendentes_validacao":
        scope="Eventos ativos criados pelo solicitante, modo colaborador"
    start,end=args.inicio,args.fim
    if start is None and reference:
        if action=="consultar_evolucao_conclusoes_equipe":
            year,month=divmod(reference.year*12+reference.month-1-11,12)
            start,end=date(year,month+1,1),reference
        elif action=="consultar_conflitos_agenda":
            start,end=reference,reference+timedelta(days=30)
    universe=("Exigências pessoa/NR do cadastro ativo autorizado antes do filtro de encaminhamento da lista."
              if action in {"consultar_matriz_conformidade_equipe","consultar_cobertura_treinamentos","consultar_indicadores_cobertura","comparar_conformidade_unidades"}
              else "Conjunto autorizado completo pelos filtros e período; independente da quantidade de linhas retornadas.")
    result["cartoes"]=BlocoIndicadores(titulo="Resumo dos dados",universo=universe,indicadores=items).model_dump(mode="json")
    result["contexto_dados"]=BlocoContextoDados(
        fonte=result["fonte"],referencia=reference,inicio=start,fim=end,escopo=scope,filtros=filters,
        criterios=[universe,"Contagens e percentuais calculados pelo backend; não certificam presença ou conformidade legal."],
        avisos=[warning],conjunto=ResumoConjunto(solicitada=args.limite,retornada=len(rows),total=total,
            limitado=result["limitado"],tem_mais=result["tem_mais"],motivo_limite=reason,unidade_total=result_unit(action))).model_dump(mode="json")
    return result

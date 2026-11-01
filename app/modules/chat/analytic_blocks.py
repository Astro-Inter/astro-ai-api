"""Apresentação de conjuntos analíticos; nenhum modelo cria métricas ou permissões."""
from app.modules.chat.schemas import (
    BlocoIndicadores,BlocoContextoDados,BlocoTabela,BlocoMatrizConformidade,
    BlocoGrafico,BlocoGraficoSetores,BlocoGraficoLinhas,
)


def build_analytic_blocks(name,data):
    blocks=[BlocoIndicadores.model_validate(data["cartoes"])]
    if data.get("linhas"):
        cls=BlocoMatrizConformidade if name=="consultar_matriz_conformidade_equipe" else BlocoTabela
        fields={key:data[key] for key in ("titulo","colunas","linhas","pagina","total_paginas","total",
                 "solicitada","retornada","limitado","tem_mais","motivo_limite","paginacao")}
        if cls is BlocoTabela:
            fields["versao"]=2
        blocks.append(cls(**fields))
    if data.get("grafico"):
        cls={"grafico_barras":BlocoGrafico,"grafico_setores":BlocoGraficoSetores,"grafico_linhas":BlocoGraficoLinhas}[data["tipo_grafico"]]
        blocks.append(cls(titulo=data.get("titulo_grafico",data["titulo"]),fonte=data["fonte"],
                          unidade=data["unidade_grafico"],dados=data["grafico"]))
    blocks.append(BlocoContextoDados.model_validate(data["contexto_dados"]))
    return blocks

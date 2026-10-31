"""Metadados de apresentação gerados pela aplicação para a revisão multimodal."""


def presentation_evidence(name: str, data: dict) -> dict | None:
    if data.get("status") != "ok":
        return None
    if name in {"buscar_meus_dados", "consultar_conformidade_usuario"}:
        return {"blocos": ["ficha_usuario"]}
    if name == "buscar_outros_usuarios" and data.get("consulta") == "listar" and data.get("usuarios"):
        count = len(data["usuarios"])
        if "retornada" in data and data["retornada"] != count:
            return None
        return {"blocos": ["tabela"], "versao_tabela": 2 if "retornada" in data else 1,
                "linhas_validadas": count, "paginacao": "local" if "retornada" in data else "servidor",
                "registros_omitidos_do_prompt": True}
    if name in {"consultar_indicadores_equipe", "consultar_grafico_pendencias_nr", "consultar_grafico_vencimentos"} and data.get("linhas"):
        kinds = ["tabela"]
        if data.get("grafico"):
            kinds.append(data.get("tipo_grafico", "grafico_barras"))
        return {"blocos": kinds, "linhas_validadas": len(data["linhas"]),
                "categorias_grafico": len(data.get("grafico", []))}
    return None

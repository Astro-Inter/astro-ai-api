import asyncio
from datetime import date, datetime
import re
import unicodedata

from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.chat.agents import invoke_agent
from app.modules.chat.router_reply import ROUTER_CLARIFICATION
from app.modules.chat.prompts.agenda import AGENDA_PROMPT_COMPLETO
from app.modules.chat.prompts.faq import FAQ_PROMPT_COMPLETO
from app.modules.chat.prompts.rh import RH_DECISAO_PROMPT_COMPLETO
from app.modules.chat.prompts.sst import SST_DECISAO_PROMPT_COMPLETO
from app.modules.chat.state import ChatState
from app.modules.chat.presentation import presentation_evidence
from app.modules.agenda.tools import (
    AgendaToolDecision, ConsultarEventosArgs, ConsultarTreinamentosArgs, TOOLS_AGENDA,
)
from app.modules.rh.tools import BuscarOutrosUsuariosArgs, RhToolDecision, TOOLS_RH
from app.modules.shared.public_sources import consultar_fontes_publicas
from app.modules.sst.tools import (
    ConsultarNrsArgs,
    ConsultarConformidadeUsuarioArgs,
    ConsultarNrsOrganizacaoArgs,
    ConsultarOrientacoesSstArgs,
    SstToolDecision,
    TOOLS_SST,
)


RH_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_RH}
SST_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_SST}
AGENDA_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_AGENDA}


def _campo_dado_proprio(message: str) -> str | None:
    """Reconhece somente perguntas completas e inequívocas sobre o próprio cadastro."""
    normalized = "".join(c for c in unicodedata.normalize("NFKD", message.casefold()) if not unicodedata.combining(c)).strip().rstrip(".!?").strip()
    if re.fullmatch(r"(?:como (?:eu )?me chamo|voce sabe (?:qual e )?meu nome)", normalized):
        return "nome"
    if normalized in {"qual unidade eu faco parte", "de qual unidade eu faco parte"}:
        return "unidade"
    match = re.fullmatch(
        r"(?:qual (?:e |eh )?(?:o |a )?|(?:me diga|me fale|mostre|informe) (?:qual e )?(?:o |a )?)"
        r"(?:meu|minha) (nome|e-?mail|cargo|unidade|perfil|modalidade)(?: (?:cadastrad[oa]|no cadastro|no astro))?",
        normalized,
    )
    return {"email": "email", "e-mail": "email", "perfil": "tipo"}.get(match.group(1), match.group(1)) if match else None


def _pedido_dos_proprios_dados(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    return _campo_dado_proprio(message) is not None or bool(re.search(
        r"\b(meus dados|meus dados pessoais|meus dados profissionais|"
        r"minhas informacoes|meu cadastro|meu perfil profissional)\b",
        normalized,
    ))


def _pedido_de_todos_os_usuarios(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    return bool(re.search(
        r"\b(todos os usuarios|todos usuarios|todos os funcionarios|"
        r"todas as pessoas do (?:meu|nosso) sistema)\b",
        normalized,
    ))


def _pedido_fontes_publicas_faq(message: str) -> bool:
    """Limita o Fetch no FAQ a pedidos de conteúdo público oficial."""
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    explicit_public_source = bool(re.search(
        r"\b(governo|fonte oficial|documento oficial|publicacao oficial|"
        r"ministerio do trabalho|mte|fundacentro|anvisa)\b",
        normalized,
    ))
    if re.search(r"\b(intern[ao]s?|da empresa|do astro)\b", normalized) \
            and not explicit_public_source:
        return False
    return bool(re.search(
        r"\b(legislacao|lei|leis|decreto|portaria|rdc|manual|guia|cartilha|"
        r"documento oficial|fonte oficial|publicacao oficial|politica (?:publica|nacional)|"
        r"atualizad\w*|ministerio do trabalho|mte|fundacentro|anvisa)\b",
        normalized,
    ))


def _filtros_nrs_organizacao(message: str) -> ConsultarNrsOrganizacaoArgs | None:
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(c)
    )
    if not re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized):
        return None
    if not re.search(r"\b(quais|quantas|liste|listar|listagem|todas|todas as|ver|mostre)\b", normalized):
        return None
    company = bool(re.search(r"\b(minha empresa|da empresa|meu workspace|do workspace|todas as unidades)\b", normalized))
    unit = bool(re.search(r"\b(minha unidade|da minha unidade|unidade atual|desta unidade)\b", normalized))
    if company == unit or re.search(r"\b(ou|outra|outro|nao)\b", normalized):
        return None
    return ConsultarNrsOrganizacaoArgs(escopo="empresa" if company else "unidade")


def _filtros_deterministicos_nrs(message: str) -> ConsultarNrsArgs | None:
    """Reconhece consultas objetivas de NR sem depender de um provedor de IA."""
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    if not re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized):
        return None
    if re.search(r"\b(empresa|workspace|unidade|unidades)\b", normalized):
        return None  # O catálogo geral não comprova vínculos organizacionais.

    numbers = [int(value) for value in re.findall(r"\bnr\s*-?\s*(\d{1,2})\b", normalized)]
    grouped = re.search(
        r"\bnrs?\s*[-:]?\s*((?:\d{1,2}(?:\s*(?:,|e)\s*\d{1,2})+))",
        normalized,
    )
    if grouped:
        numbers.extend(int(value) for value in re.findall(r"\d{1,2}", grouped.group(1)))
    numbers = list(dict.fromkeys(numbers))
    if len(numbers) > 10:
        return None

    page_match = re.search(r"\bpagina\s+(\d{1,3})\b", normalized)
    page = int(page_match.group(1)) if page_match else 1
    field_markers = {
        "objetivo": ("objetiv", "finalidade"),
        "descricao": ("descri", "conteudo", "explica", "detalh"),
        "aplicabilidade": ("aplicab", "aplica"),
        "revogada": ("revog", "vigent", "vigencia"),
        "tempo_reciclagem_meses": ("reciclagem", "reciclar"),
        "ultima_atualizacao": ("ultima atualizacao", "atualizada", "atualizacao"),
        "data_criacao": ("data de criacao", "criada", "criacao"),
        "usabilidade": ("usabilidade", "publico", "quem pode usar"),
    }
    fields = [
        field for field, markers in field_markers.items()
        if any(marker in normalized for marker in markers)
    ]

    if numbers:
        mode = "detalhar" if len(numbers) == 1 or fields else "listar"
        return ConsultarNrsArgs(
            numeros=numbers,
            campos=fields,
            modo=mode,
            pagina=page,
            limite=50,
        )

    listing = re.search(
        r"\b(quais|liste|listar|listagem|todas|todos)\b", normalized,
    )
    status_query = any(marker in normalized for marker in ("revog", "vigent", "vigencia"))
    if not listing and not status_query:
        return None
    revoked = None
    if any(marker in normalized for marker in ("vigent", "vigencia", "nao revogad")):
        revoked = False
    elif "revog" in normalized:
        revoked = True
    return ConsultarNrsArgs(
        revogada=revoked,
        modo="listar",
        pagina=page,
        limite=50,
    )


def _pedido_nrs_obrigatorias(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    mentions_nr = bool(re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized))
    obligation = bool(re.search(
        r"\b(obrigatori\w*|preciso cumprir|devo cumprir|aplicaveis? (?:ao|a) meu|"
        r"exigid[ao]s?|para (?:o )?meu cargo|para (?:a )?minha funcao)\b",
        normalized,
    ))
    return mentions_nr and obligation


def _pedido_conformidade_terceiro(message: str) -> bool:
    normalized = "".join(c for c in unicodedata.normalize("NFKD", message.casefold()) if not unicodedata.combining(c))
    return bool(re.search(r"\b(?:conformidade|ficha)\b", normalized) and not re.search(r"\b(?:minha|meu|para mim)\b", normalized))


def _filtros_conformidade(message: str, history: list[dict] | None = None) -> ConsultarConformidadeUsuarioArgs | None:
    if not _pedido_conformidade_terceiro(message):
        return None
    match = re.search(r"\b(?:conformidade|ficha)\s+(?:de|do|da)\s+(?:(?:funcion[aá]ri[oa]|usu[aá]ri[oa]|pessoa)\s+)?(.+?)[?!.,]*$", message, re.IGNORECASE)
    person = match.group(1).strip() if match else None
    if person is None or person.casefold() in {"ela", "ele", "dela", "dele", "essa pessoa", "esta pessoa"}:
        # Só resolve automaticamente uma referência ao último resultado de RH
        # no formato produzido pela aplicação, contendo exatamente uma pessoa.
        if not re.search(r"\b(?:del[ae]|dess[ae] pessoa|ess[ae] pessoa|el[ae])\b", message, re.IGNORECASE):
            return None
        previous = next((item for item in reversed(history or []) if item["role"] == "assistant"), {})
        focus = previous.get("pessoa_em_foco")
        if isinstance(focus, str):
            return ConsultarConformidadeUsuarioArgs(pessoa=focus)
        last_answer = previous.get("content", "")
        if not re.match(r"Encontrei 1 usuário\(s\):", last_answer):
            return None
        emails = set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", last_answer))
        if len(emails) != 1:
            return None
        person = emails.pop()
    return ConsultarConformidadeUsuarioArgs(pessoa=person)


def _pedido_situacao_nrs(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    mentions_nr = bool(re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized))
    personal_context = bool(re.search(
        r"\b(minhas?|meus?|para mim|eu preciso|preciso|devo|tenho que)\b",
        normalized,
    ))
    situation = bool(re.search(
        r"\b(situacao|status|valid[ao]s?|validade|em dia|pendent\w*|vencid\w*|"
        r"realizar|renovar|renovacao|reciclagem)\b",
        normalized,
    ))
    return mentions_nr and personal_context and situation


def _filtros_eventos(message: str) -> ConsultarEventosArgs | None:
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(c)
    )
    if not re.search(r"\beventos?\b", normalized):
        return None
    if re.search(r"\b(google|calendar|nao|como|criar|crie|adicion\w*|coloque|cancel\w*|alter\w*|exclu\w*|outro|outra|colega)\b", normalized):
        return None
    if re.search(r"\b(hoje|amanha|semana|mes|ano|dia)\b|\d{2}/\d{2}", normalized):
        return None
    if not re.search(r"\b(proxim[oa]s?|meus?|tenho)\b", normalized):
        return None
    page = re.search(r"\bpagina\s+(\d+)\b", normalized)
    try:
        return ConsultarEventosArgs(
            proximos=not bool(re.search(r"\b(todos|historico|passados|anteriores)\b", normalized)),
            limite=1 if re.search(r"\bproximo evento\b", normalized) else 5,
            pagina=int(page.group(1)) if page else 1,
        )
    except ValueError:
        return None


def _filtros_treinamentos(message: str) -> ConsultarTreinamentosArgs | None:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    if not re.search(r"\b(treinamentos?|capacitac(?:ao|oes))\b", normalized):
        return None
    if re.search(
        r"\b(google (?:calendar|agenda)|coloque|adicion\w*|agend\w*|marque|crie)\b",
        normalized,
    ):
        return None
    if not re.search(
        r"\b(meus?|minhas?|preciso|tenho|devo|fazer|realizar|"
        r"pendentes?|atribuidos?|inscritos?|conclui|concluidos?)\b", normalized,
    ):
        return None
    if re.search(r"\b(conclui|concluidos?|ja fiz|finalizei)\b", normalized):
        situation = "concluidos"
    elif re.search(r"\b(todos|historico)\b", normalized) and not re.search(
        r"\b(preciso|pendentes?|realizar|fazer)\b", normalized,
    ):
        situation = "todos"
    else:
        situation = "a_realizar"
    page_match = re.search(r"\bpagina\s+(\d+)\b", normalized)
    try:
        return ConsultarTreinamentosArgs(
            situacao=situation,
            pagina=int(page_match.group(1)) if page_match else 1,
        )
    except ValueError:
        return None


def _data_legivel(value, *, horario=False):
    """Display an ISO calendar date without guessing a timezone or changing data."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            parsed = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            parsed = value
        if not isinstance(parsed, (date, datetime)):
            return None
        text = parsed.strftime("%d/%m/%Y")
        if horario and isinstance(parsed, datetime):
            text += parsed.strftime(" às %H:%M")
        return text
    except ValueError:
        return None


def _formatar_usuarios(result: dict) -> str:
    if result.get("consulta") == "contagem" and result.get("status") in {"ok", "sem_dados"}:
        return f"Encontrei {result['total']} usuário(s) no seu escopo autorizado, com os filtros informados. Essa contagem não inclui você."
    if result.get("status") == "sem_dados":
        return "Não encontrei usuários com os filtros informados."
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar os usuários no momento.")

    if "retornada" in result:
        text = (f"Retornados {result['retornada']} usuário(s) de {result['total']} disponíveis "
                f"no escopo autorizado; quantidade solicitada: {result['solicitada']}.")
        if result.get("limitado"):
            text += " Resultado limitado por segurança (" + result['motivo_limite'] + ")."
        elif result.get("tem_mais"):
            text += " O conjunto retornado é parcial, conforme a quantidade solicitada."
        return text
    users = result["usuarios"]
    title = f"Encontrei {len(users)} usuário(s):"
    lines = []
    for user in users:
        details = [str(item) for item in (user.get("cargo"), user.get("unidade"), user.get("modalidade")) if item]
        description = ", ".join(details)
        profile = {"COLABORADOR": "Colaborador", "GESTOR": "Gestor", "GESTOR_WORKSPACE": "Gestor do workspace"}.get(user.get("tipo"))
        profile_text = f" Perfil: {profile}." if profile else ""
        contact = f" E-mail: {user['email']}." if user.get("email") else ""
        lines.append(f"{user['nome']}: {description}." + profile_text + contact)
    if "pagina" in result:
        lines.append(f"Retornados {len(users)} de {result['total']} usuário(s) disponíveis.")
        if not users:
            lines.append("Nenhum usuário retornado neste recorte; o total disponível permanece informado.")
        elif len(users) < result["total"]:
            lines.append("O conjunto retornado é parcial.")
    return "\n".join([title, *lines])


def _formatar_meus_dados(result: dict) -> str:
    if result.get("status") == "sem_dados":
        return result.get("mensagem", "Não encontrei seus dados cadastrais.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar seus dados no momento.")

    data = result["dados"]
    labels = {
        "nome": "Nome", "email": "E-mail", "cpf": "CPF", "tipo": "Perfil",
        "cargo": "Cargo", "unidade": "Unidade", "modalidade": "Modalidade",
    }
    lines = [f"- {labels[field]}: {value}" for field, value in data.items() if field in labels and value is not None]
    return "\n".join(["Estes são os seus dados:", *lines])


def _formatar_nrs(result: dict) -> str:
    if result.get("status") == "sem_dados":
        return "Não encontrei NRs com os filtros informados."
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar as NRs no momento.")

    pagination = result.get("paginacao", {})
    primary_source = result.get("fonte") or {}
    source_url = primary_source.get("url")
    source_footer = (
        f"Fonte oficial: [Ministério do Trabalho e Emprego]({source_url})."
        if source_url
        else "Fonte utilizada: base interna de NRs do Astro; a fonte oficial estava indisponível."
    )
    if result.get("modo") == "listar":
        total = pagination.get("total", result.get("quantidade", 0))
        lines = []
        for nr in result["nrs"]:
            details = [nr.get("situacao")]
            suffix = f". Situação da norma: {nr['situacao'].lower()}." if any(details) else ""
            lines.append(f"- NR-{nr.get('numero')} — {nr.get('nome', 'Sem nome')}{suffix}")
        footer = [source_footer]
        if len(result["nrs"]) < total:
            footer.insert(0, "O conjunto retornado é parcial.")
        return "\n".join([
            f"Retornadas {len(result['nrs'])} de {total} NR(s) disponíveis:",
            *lines,
            *footer,
        ])

    labels = {
        "nome": "Nome", "objetivo": "Objetivo", "descricao": "Descrição",
        "aplicabilidade": "Aplicabilidade", "revogada": "Revogada",
        "tempo_reciclagem_meses": "Reciclagem (meses)",
        "usabilidade": "Usabilidade",
    }
    sections = []
    for nr in result["nrs"]:
        number = nr.get("numero")
        title = f"NR-{number}"
        if nr.get("nome"):
            title += f" — {nr['nome']}"
        details = []
        if nr.get("situacao"):
            details.append(f"- Situação na fonte oficial: {nr['situacao'].lower()}")
        if nr.get("resumo_oficial"):
            details.append(f"- Informação oficial: {nr['resumo_oficial']}")
        for field, label in labels.items():
            if field == "nome" or field not in nr:
                continue
            value = nr[field]
            if value is None:
                continue
            if field == "revogada":
                value = "Sim" if value else "Não"
            details.append(f"- {label}: {value}")
        if nr.get("fonte_oficial"):
            details.append(
                f"- Fonte oficial: [Ministério do Trabalho e Emprego]({nr['fonte_oficial']})"
            )
        elif source_url:
            details.append(
                f"- Fonte oficial: [Ministério do Trabalho e Emprego]({source_url})"
            )
        sections.append("\n".join([title, *details]))
    return "\n\n".join(sections)


def _evidencia_compacta_nrs(result: dict) -> dict:
    """Evita repetir no prompt do Juiz os textos já presentes na candidata."""
    return {
        "status": result.get("status"),
        "quantidade": result.get("quantidade", 0),
        "modo": result.get("modo"),
        "numeros": [nr.get("numero") for nr in result.get("nrs", [])],
        "campos_retornados": sorted({
            field
            for nr in result.get("nrs", [])
            for field in nr
            if field != "numero"
        }),
        "fonte": result.get("fonte"),
        "fontes": result.get("fontes", []),
        "origem_principal": result.get("origem_principal"),
        "paginacao": result.get("paginacao"),
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def _formatar_orientacoes_sst(result: dict) -> str:
    if result.get("status") == "sem_dados":
        return (
            "Não encontrei uma orientação oficial relevante para esse assunto "
            "nos catálogos consultados."
        )
    if result.get("status") != "ok":
        return result.get(
            "mensagem", "Não foi possível consultar as orientações oficiais no momento.",
        )

    lines = [f"Orientações oficiais relacionadas a {result['termo']}:"]
    for source in result.get("fontes", []):
        for snippet in source.get("trechos", []):
            lines.append(f"- {source['orgao']}: {snippet}")
        update = (
            f" (atualizada em {source['atualizado_em']})"
            if source.get("atualizado_em") else ""
        )
        lines.append(f"  Fonte: [{source['titulo']}]({source['url']}){update}")
    return "\n".join(lines)


def _evidencia_orientacoes_sst(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "termo": result.get("termo"),
        "quantidade": result.get("quantidade", 0),
        "fontes": result.get("fontes", []),
        "protocolo": result.get("protocolo"),
        "formatacao": "resposta gerada deterministicamente a partir dos trechos oficiais",
    }


def _formatar_nrs_organizacao(result: dict) -> str:
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar as NRs da organização.")
    scope = (
        f"empresa {result['empresa']} (união de todas as unidades)"
        if result["escopo"] == "empresa" else f"unidade {result['unidade']}"
    )
    if not result["nrs"]:
        return f"Não encontrei NRs vinculadas à {scope} no Astro."
    lines = [f"NRs vinculadas à {scope}: {result['quantidade']} norma(s) distinta(s).", ""]
    lines += [
        f"- NR-{nr['numero']}: {nr['titulo']}"
        + (" (revogada)" if nr.get("revogada") else "")
        for nr in result["nrs"]
    ]
    lines += ["", "Esses vínculos não comprovam a conformidade da empresa nem a obrigatoriedade para o seu cargo."]
    return "\n".join(lines)


def _formatar_nrs_obrigatorias(result: dict) -> str:
    if result.get("status") == "nao_aplicavel":
        return result["mensagem"]
    if result.get("status") == "sem_dados":
        return result.get("mensagem", "Não encontrei seu cadastro funcional.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar suas NRs obrigatórias.")

    nrs = result["nrs"]
    header = (
        f"Para o cargo {result['cargo']}, na unidade {result['unidade']}, "
        f"encontrei {len(nrs)} NR(s) vigente(s) vinculada(s):"
    )
    if not nrs:
        return "\n".join([
            header,
            "Nenhuma NR vigente está vinculada ao seu cargo.",
        ])
    lines = []
    for nr in nrs:
        recycling = nr.get("tempo_reciclagem_meses")
        suffix = f". A reciclagem é a cada {recycling} meses." if recycling is not None else ""
        lines.append(f"- NR-{nr['numero']} — {nr['titulo']}{suffix}")
    return "\n".join([
        header,
        *lines,
    ])


def _evidencia_nrs_obrigatorias(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "cargo": result.get("cargo"),
        "unidade": result.get("unidade"),
        "quantidade": result.get("quantidade", 0),
        "nrs": result.get("nrs", []),
        "fonte": result.get("fonte"),
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def _formatar_situacao_nrs(result: dict) -> str:
    if result.get("consulta_terceiro"):
        if result.get("status") != "ok":
            return result.get("mensagem", "Não foi possível consultar a conformidade.")
        formatted = _formatar_situacao_nrs({k: v for k, v in result.items() if k != "consulta_terceiro"})
        formatted = formatted.replace("Nenhuma NR vigente está vinculada ao seu cargo.", "Nenhuma NR vigente está vinculada ao cargo cadastrado desta pessoa.")
        return f"NRs de {result['usuario']}:\n\n{formatted}"
    if result.get("status") == "nao_aplicavel":
        return result["mensagem"]
    if result.get("status") == "sem_dados":
        return result.get("mensagem", "Não encontrei seu cadastro funcional.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar a situação das suas NRs.")

    nrs = result["nrs"]
    header = (
        f"Estas são as NRs do cargo {result['cargo']}, "
        f"na unidade {result['unidade']}:"
    )
    if not nrs:
        return "\n".join([header, "Nenhuma NR vigente está vinculada ao seu cargo."])

    descriptions = {
        "VIGENTE": "está em dia",
        "PENDENTE": "tem uma atividade pendente",
        "RENOVACAO_NECESSARIA": "precisa ser renovada",
        "REALIZACAO_NECESSARIA": "precisa ser realizada",
    }
    actions = {
        "CONCLUIR_PENDENCIA": "Conclua a atividade pendente.",
        "RENOVAR_EM_BREVE": "Programe a renovação em breve.",
        "RENOVAR": "Providencie a renovação.",
        "REALIZAR": "Realize a atividade exigida para essa NR.",
    }
    paragraphs = []
    missing_validity = False
    for nr in nrs:
        description = descriptions.get(nr.get("situacao"), "está sem situação confirmada")
        # An inconsistent missing date must not become an assurance of validity.
        validity = _data_legivel(nr.get("data_validade"))
        if nr.get("situacao") == "VIGENTE" and validity is None:
            description = "está sem validade confirmada"
        title = f"NR-{nr['numero']} — {nr['titulo']}"
        sentences = [f"{title}: {description}."]
        action = actions.get(nr.get("acao_necessaria"))
        if action:
            sentences.append(action)
        if validity:
            sentences.append(f"Validade: {validity}.")
        else:
            missing_validity = True
        paragraphs.append(" ".join(sentences))
    if missing_validity:
        paragraphs.append("Algumas NRs estão sem data de validade. Não é possível confirmar se estão em dia.")
    return "\n\n".join([header, *paragraphs])



def _event_public_link(value):
    """Do not present a localhost/test link as a usable event destination.

    The registered value is never rewritten; only its presentation is omitted.
    """
    from urllib.parse import urlparse
    from ipaddress import ip_address
    if not isinstance(value,str) or not value or len(value)>300:
        return None
    try:
        parsed=urlparse(value)
        host=parsed.hostname or ""
        if parsed.scheme not in {"http","https"} or not host or parsed.username or parsed.password:
            return None
        if host.lower().rstrip('.')=='localhost' or host.lower().rstrip('.').endswith('.localhost'):
            return None
        try:
            address=ip_address(host)
            if address.is_loopback or address.is_unspecified:return None
        except ValueError:
            pass
        return value
    except ValueError:
        return None


def _formatar_treinamentos(result: dict) -> str:
    if result.get("status") == "sem_dados":
        labels = {
            "a_realizar": "Não encontrei treinamentos ativos pendentes atribuídos a você.",
            "concluidos": "Não encontrei treinamentos concluídos atribuídos a você.",
            "todos": "Não encontrei treinamentos atribuídos a você.",
        }
        return labels.get(result.get("situacao"), "Não encontrei treinamentos.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar seus treinamentos.")

    lines = ["Seus treinamentos atribuídos:"]
    status_labels = {
        "PENDENTE": "pendente", "REJEITADO": "rejeitada", "CONCLUIDO": "concluída",
    }
    for training in result["treinamentos"]:
        reference = training.get("evento_id")
        details = [f"{training['titulo']}.", f"Turma {training['turma']}."]
        start_date = _data_legivel(training.get("data_inicio"), horario=True)
        end_date = _data_legivel(training.get("data_termino"), horario=True)
        if start_date:
            details.append(f"Começa em {start_date}.")
        if end_date:
            details.append(f"Termina em {end_date}.")
        participation = status_labels.get(training['status_participacao'], "não informada")
        details.append(f"Sua participação está {participation}.")
        event_status = {"ATIVO": "ativo", "CANCELADO": "cancelado", "CONCLUIDO": "concluído"}.get(training['status_evento'])
        if event_status:
            details.append(f"O evento está {event_status}.")
        if training.get("nr"):
            details.append(f"Relacionado à NR-{training['nr']['numero']} — {training['nr']['titulo']}.")
        completion = {"GESTOR": "A conclusão depende da análise do gestor.",
                      "AUTOMATICO": "A conclusão é automática.",
                      "COLABORADOR": "A conclusão é feita pelo participante."}.get(training['modo_conclusao'])
        if completion:
            details.append(completion)
        if training["evidencia_obrigatoria"]:
            details.append("É necessário enviar uma evidência para comprovar a realização.")
        validity = _data_legivel(training.get("data_validade"))
        if validity:
            details.append(f"Validade: {validity}.")
        if type(reference) is int:
            details.append(f"Para selecionar este evento, use a referência do evento: {reference} (evento_id).")
        lines.append(" ".join(details))
        if training.get("descricao"):
            lines.append(f"  {training['descricao'][:160]}")
        if training.get("motivo_rejeicao"):
            lines.append(f"  Motivo da rejeição: {training['motivo_rejeicao'][:160]}")
        link=_event_public_link(training.get("link_externo"))
        if link:
            lines.append(f"  Link: {link}")
        elif training.get("link_externo"):
            lines.append("  Link cadastrado não disponível para abertura externa; consulte o evento no aplicativo.")
    if not result["treinamentos"]:
        lines.append("Nenhum treinamento nesta página. Tente uma página anterior.")
    elif result["pagina"] < result["total_paginas"]:
        lines.append(f"Para ver mais treinamentos, peça a página {result['pagina'] + 1}.")
    return "\n\n".join(lines)


def _evidencia_treinamentos(result: dict) -> dict:
    """Envia ao Juiz só os campos que podem aparecer na resposta formatada."""
    visible_fields = (
        "titulo", "evento_id", "turma", "data_inicio", "data_termino", "nr",
        "status_evento", "status_participacao", "modo_conclusao",
        "evidencia_obrigatoria", "data_validade",
    )
    trainings = []
    for training in result.get("treinamentos", []):
        item = {field: training.get(field) for field in visible_fields}
        if training.get("descricao"):
            item["descricao"] = training["descricao"][:160]
        if training.get("motivo_rejeicao"):
            item["motivo_rejeicao"] = training["motivo_rejeicao"][:160]
        link=_event_public_link(training.get("link_externo"))
        if link:
            item["link_externo"] = link
        elif training.get("link_externo"):
            item["aviso_link"] = "Link cadastrado não disponível para abertura externa; consulte o evento no aplicativo."
        trainings.append(item)
    return {
        "status": result.get("status"),
        "situacao": result.get("situacao"),
        "pagina": result.get("pagina"),
        "total_paginas": result.get("total_paginas"),
        "treinamentos": trainings,
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def _formatar_eventos(result: dict) -> str:
    upcoming = result.get("proximos")
    if result.get("status") == "sem_dados":
        return (
            "Não encontrei próximos eventos atribuídos a você no Astro."
            if upcoming else "Não encontrei eventos atribuídos a você no Astro."
        ) + " Sem eventos neste conjunto, não há evento_id para selecionar um resumo."
    if result.get("status") != "ok":
        return "Não foi possível consultar seus eventos no Astro. " + result.get("mensagem", "")
    trainings = {**result, "treinamentos": result.get("eventos", [])}
    return _formatar_treinamentos(trainings).replace(
        "Seus treinamentos atribuídos:",
        "Seus próximos eventos no Astro:" if upcoming else "Seus eventos no Astro:",
    ).replace("treinamento", "evento")


def _referencias_eventos(result):
    """Selection needs titles and references, not descriptions or external links."""
    if result.get('status')!='ok':
        return _formatar_eventos(result)
    events={}
    for event in result.get('eventos',[]):
        reference=event.get('evento_id')
        if type(reference) is int and reference>0 and isinstance(event.get('titulo'),str):
            events.setdefault(reference,event['titulo'])
    if not events:
        return 'A consulta não retornou referências evento_id utilizáveis para selecionar um resumo.'
    lines=['Eventos atribuídos a você para selecionar um resumo:']
    lines.extend(f'{title}. Para selecionar, use a referência do evento: {reference} (evento_id).' for reference,title in events.items())
    lines.append('Escolha pelo evento_id, por exemplo: Faça um resumo do evento '+str(next(iter(events)))+'. O acesso será verificado novamente.')
    if result.get('pagina',1)<result.get('total_paginas',1):
        lines.append(f"Para mais eventos, consulte a página {result['pagina']+1} da sua agenda.")
    return '\n'.join(lines)

def _evidencia_situacao_nrs(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "usuario": result.get("usuario"),
        "mensagem": result.get("mensagem"),
        "cargo": result.get("cargo"),
        "unidade": result.get("unidade"),
        "data_referencia": result.get("data_referencia"),
        "quantidade": result.get("quantidade", 0),
        "nrs": result.get("nrs", []),
        "fonte": result.get("fonte"),
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def build_rh_graph(model: AgentModel):
    async def decide(state: ChatState):
        prepared = state.get("rh_decision")
        if isinstance(prepared, RhToolDecision):
            return {"rh_route": "tool", "agentes_chamados": state["agentes_chamados"] + ["rh"]}
        if _pedido_dos_proprios_dados(state["mensagem"]):
            return {
                "rh_route": "tool",
                "rh_decision": RhToolDecision(acao="buscar_meus_dados"),
                "agentes_chamados": state["agentes_chamados"] + ["rh"],
            }
        if _pedido_de_todos_os_usuarios(state["mensagem"]):
            return {
                "rh_route": "tool",
                "rh_decision": RhToolDecision(
                    acao="buscar_outros_usuarios",
                    filtros=BuscarOutrosUsuariosArgs(limite=200),
                ),
                "agentes_chamados": state["agentes_chamados"] + ["rh"],
            }
        decision = await invoke_agent(
            model, "rh", RH_DECISAO_PROMPT_COMPLETO, state, RhToolDecision,
        )

        # Unsupported personal selectors must not silently become a broad list.
        if decision.acao == "buscar_outros_usuarios" and state["usuario_atual"].role in {"ADMIN", "GESTOR", "GESTOR_WORKSPACE"}:
            emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", state["mensagem"])
            opaque = re.search(r"\b(?:uid|firebase_uid|identificador)\s*[:=]?\s*[A-Za-z0-9_-]{16,128}\b",
                               state["mensagem"], re.I) or re.search(
                                   r"\b(?:dados(?: cadastrais)?|cadastro|ficha) (?:de|do|da) [A-Za-z0-9_-]{20,128}\b",
                                   state["mensagem"], re.I)
            name = decision.filtros.nome if decision.filtros else None
            supplied_name = name and name.casefold() in state["mensagem"].casefold() and name not in emails
            if (emails or opaque) and not supplied_name:
                return {"rh_route": "fim", "resultado": {"dominio": "rh", "intencao": "consultar", "status": "esclarecer",
                        "resposta": "Para localizar essa pessoa nesta consulta, informe o nome completo. O filtro de cadastro disponível aqui não pesquisa por e-mail ou UID.",
                        "recomendacao": "", "esclarecer": "Qual é o nome completo da pessoa?"},
                        "agentes_chamados": state["agentes_chamados"] + ["rh"]}
        if decision.acao == "responder":
            return {
                "rh_route": "fim",
                "resultado": decision.resposta.model_dump(exclude_none=True),
                "agentes_chamados": state["agentes_chamados"] + ["rh"],
            }
        return {
            "rh_route": "tool",
            "rh_decision": decision,
            "agentes_chamados": state["agentes_chamados"] + ["rh"],
        }

    async def use_tool(state: ChatState):
        decision = state["rh_decision"]
        tool_name = decision.acao
        tool_input = (
            decision.filtros.model_dump()
            if tool_name == "buscar_outros_usuarios" and decision.filtros is not None
            else {}
        )
        result = await RH_TOOLS[tool_name].ainvoke(
            tool_input,
            config={"configurable": {"usuario_atual": state["usuario_atual"].model_dump()}},
        )
        field = _campo_dado_proprio(state["mensagem"])
        if tool_name == "buscar_meus_dados" and field and result.get("status") == "ok":
            # Não envia CPF ou outros dados não solicitados aos agentes revisores.
            result = {**result, "dados": {field: result.get("dados", {}).get(field)}}
            if result["dados"][field] is None:
                result = {**result, "status": "sem_dados", "mensagem": f"Não encontrei o campo {field} no seu cadastro."}
        if tool_name == "buscar_meus_dados" and not field and result.get("status") == "ok":
            # O cartão completo contém somente campos profissionais suportados.
            allowed = {"nome", "email", "tipo", "cargo", "unidade", "modalidade"}
            result = {**result, "dados": {key: value for key, value in result.get("dados", {}).items()
                                         if key in allowed}}
        tool_status = result.get("status")
        statuses = {
            "ok": "concluido",
            "sem_dados": "sem_dados",
            "indisponivel": "indisponivel",
            "erro": "nao_autorizado",
            "nao_autorizado": "nao_autorizado",
        }
        if tool_name == "buscar_meus_dados":
            messages = {
                "ok": "Consulta dos dados do usuário autenticado concluída.",
                "sem_dados": "O usuário autenticado não foi encontrado no cadastro.",
                "indisponivel": "Não foi possível consultar seus dados no momento.",
                "erro": "Não foi possível identificar o usuário autenticado.",
            }
        else:
            messages = {
                "ok": f"Consulta concluída com {result.get('quantidade', 0)} usuário(s).",
                "sem_dados": "Nenhum usuário foi encontrado com os filtros informados.",
                "indisponivel": "Não foi possível consultar os usuários no momento.",
                "erro": "Não foi possível identificar o usuário autorizado para a consulta.",
                "nao_autorizado": result.get(
                    "mensagem", "Seu perfil não permite consultar outros usuários."
                ),
            }
        specialist_result = {
            "dominio": "rh",
            "intencao": "consultar",
            "status": statuses.get(tool_status, "indisponivel"),
            "resposta": messages.get(
                tool_status, "Não foi possível confirmar o resultado da consulta.",
            ),
            "recomendacao": "",
            "evidencia_tool": {
                "nome": tool_name,
                "resultado": ({key: value for key, value in result.items() if key != "usuarios"}
                              if tool_name == "buscar_outros_usuarios" and "retornada" in result else result),
            },
        }
        presentation = presentation_evidence(tool_name, result) if not field else None
        if presentation:
            specialist_result["apresentacao"] = presentation
        return {
            "resultado_tool": result,
            "resultado": specialist_result,
            "resposta_deterministica": True,
            "consulta_rh": (
                decision.filtros.model_dump()
                if tool_name == "buscar_outros_usuarios" and result.get("status") in {"ok", "sem_dados"}
                else state.get("consulta_rh") if tool_name != "buscar_outros_usuarios" else None
            ),
            "candidato": (
                _formatar_meus_dados(result)
                if tool_name == "buscar_meus_dados"
                else _formatar_usuarios(result)
            ),
            "agentes_chamados": state["agentes_chamados"] + [tool_name],
        }

    graph = StateGraph(ChatState)
    graph.add_node("decidir", decide)
    graph.add_node("usar_tool_rh", use_tool)
    graph.add_edge(START, "decidir")
    graph.add_conditional_edges("decidir", lambda state: state["rh_route"], {
        "tool": "usar_tool_rh", "fim": END,
    })
    graph.add_edge("usar_tool_rh", END)
    return graph.compile(name="subgrafo_rh")


def build_sst_graph(model: AgentModel):
    async def decide(state: ChatState):
        from app.modules.chat.current_intent import general_nr_question, personal_pending_kind
        if personal_pending_kind(state["mensagem"]) == "sst":
            return {"sst_route": "tool", "sst_decision": SstToolDecision(acao="consultar_situacao_nrs"),
                    "agentes_chamados": state["agentes_chamados"] + ["sst"]}
        if general_nr_question(state["mensagem"]):
            return {"sst_route": "tool",
                    "sst_decision": SstToolDecision(acao="consultar_orientacoes_sst",
                        filtros=ConsultarOrientacoesSstArgs(termo="Normas Regulamentadoras", fontes=["mte_normas"])),
                    "agentes_chamados": state["agentes_chamados"] + ["sst"]}
        conformidade = _filtros_conformidade(state["mensagem"], state["historico"])
        if conformidade is not None:
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(acao="consultar_conformidade_usuario", filtros=conformidade),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        organization = _filtros_nrs_organizacao(state["mensagem"])
        if organization is not None:
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(acao="consultar_nrs_organizacao", filtros=organization),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        if _pedido_situacao_nrs(state["mensagem"]):
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(acao="consultar_situacao_nrs"),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        if _pedido_nrs_obrigatorias(state["mensagem"]):
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(acao="consultar_nrs_obrigatorias"),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        deterministic_filters = _filtros_deterministicos_nrs(state["mensagem"])
        if deterministic_filters is not None:
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(
                    acao="consultar_nrs", filtros=deterministic_filters,
                ),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        decision = await invoke_agent(
            model, "sst", SST_DECISAO_PROMPT_COMPLETO, state, SstToolDecision,
        )
        if decision.acao == "responder":
            return {
                "sst_route": "fim",
                "resultado": decision.resposta.model_dump(exclude_none=True),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        return {
            "sst_route": "tool",
            "sst_decision": decision,
            "agentes_chamados": state["agentes_chamados"] + ["sst"],
        }

    async def use_tool(state: ChatState):
        decision = state["sst_decision"]
        tool_name = decision.acao
        tool_input = decision.filtros.model_dump() if decision.filtros is not None else {}
        result = await SST_TOOLS[tool_name].ainvoke(
            tool_input,
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        tool_status = result.get("status")
        statuses = {
            "ok": "concluido",
            "sem_dados": "sem_dados",
            "indisponivel": "indisponivel",
            "erro": "nao_autorizado",
            "nao_aplicavel": "nao_autorizado",
            "nao_autorizado": "nao_autorizado",
            "esclarecer": "esclarecer",
        }
        if tool_name == "consultar_orientacoes_sst":
            messages = {
                "ok": (
                    f"Consulta concluída com {result.get('quantidade', 0)} "
                    "trecho(s) oficial(is)."
                ),
                "sem_dados": "Nenhuma orientação oficial relevante foi encontrada.",
                "indisponivel": "Não foi possível consultar as fontes oficiais no momento.",
                "nao_autorizado": result.get(
                    "mensagem", "A fonte solicitada não está autorizada.",
                ),
            }
        else:
            messages = {
                "ok": f"Consulta concluída com {result.get('quantidade', 0)} NR(s).",
                "sem_dados": "Nenhuma NR foi encontrada com os filtros informados.",
                "indisponivel": "Não foi possível consultar as NRs no momento.",
                "erro": "Não foi possível identificar o usuário autenticado.",
                "nao_aplicavel": result.get(
                    "mensagem", "A consulta não se aplica ao perfil autenticado.",
                ),
                "esclarecer": result.get("mensagem", "Informe o nome ou e-mail da pessoa."),
                "nao_autorizado": result.get("mensagem", "Consulta não autorizada."),
            }
        if tool_name in {"consultar_nrs", "consultar_orientacoes_sst"}:
            source_items = [
                {
                    "titulo": source.get("titulo", "Fonte oficial de SST"),
                    "referencia": source.get("url", "Base interna do Astro"),
                }
                for source in result.get("fontes", [])
            ]
        else:
            source_items = [{
                "titulo": "Registros funcionais e de conformidade",
                "referencia": "Base interna do Astro",
            }]
        specialist_result = {
            "dominio": "sst",
            "intencao": "consultar",
            "status": statuses.get(tool_status, "indisponivel"),
            "resposta": messages.get(
                tool_status, "Não foi possível confirmar o resultado da consulta.",
            ),
            "recomendacao": "",
            "fontes": source_items,
            "evidencia_tool": {"nome": tool_name, "resultado": (
                _evidencia_orientacoes_sst(result)
                if tool_name == "consultar_orientacoes_sst"
                else result
                if tool_name == "consultar_nrs_organizacao"
                else _evidencia_situacao_nrs(result)
                if tool_name in {"consultar_situacao_nrs", "consultar_conformidade_usuario"}
                else _evidencia_nrs_obrigatorias(result)
                if tool_name == "consultar_nrs_obrigatorias"
                else _evidencia_compacta_nrs(result)
            )},
        }
        presentation = presentation_evidence(tool_name, result)
        if presentation:
            specialist_result["apresentacao"] = presentation
        return {
            "resultado_tool": result,
            "resultado": specialist_result,
            "resposta_deterministica": True,
            "candidato": (
                _formatar_orientacoes_sst(result)
                if tool_name == "consultar_orientacoes_sst"
                else _formatar_nrs_organizacao(result)
                if tool_name == "consultar_nrs_organizacao"
                else _formatar_situacao_nrs(result)
                if tool_name in {"consultar_situacao_nrs", "consultar_conformidade_usuario"}
                else _formatar_nrs_obrigatorias(result)
                if tool_name == "consultar_nrs_obrigatorias"
                else _formatar_nrs(result)
            ),
            "agentes_chamados": state["agentes_chamados"] + [tool_name],
        }

    graph = StateGraph(ChatState)
    graph.add_node("decidir", decide)
    graph.add_node("usar_tool_sst", use_tool)
    graph.add_edge(START, "decidir")
    graph.add_conditional_edges("decidir", lambda state: state["sst_route"], {
        "tool": "usar_tool_sst", "fim": END,
    })
    graph.add_edge("usar_tool_sst", END)
    return graph.compile(name="subgrafo_sst")


def pedido_grafico_treinamentos(message: str) -> bool:
    text = "".join(c for c in unicodedata.normalize("NFKD", message.casefold()) if not unicodedata.combining(c))
    if re.search(r"\b(concluid[oa]s?|mensal)\b", text) and not re.search(r"\b(linhas?|evolucao|mensal)\b", text):
        return False
    return bool(re.search(r"\b(grafico|dashboard|visualizacao|rosca|donut)\b", text)
                and re.search(r"\b(treinamentos?|capacitac(?:ao|oes)|conclusoes)\b", text)
                and re.search(r"\b(meus?|minhas?|para mim)\b", text)
                and not re.search(r"\b(empresa|unidade|equipe|todos|outros|ano|mes|periodo|vencid[oa]s?|pendentes?|rejeitad[oa]s?)\b|\d", text))


def build_agenda_graph(model: AgentModel):
    async def decide(state: ChatState):
        prepared_decision = state.get("agenda_decision")
        if pedido_grafico_treinamentos(state["mensagem"]):
            return {"agenda_route": "tool", "agenda_decision": "grafico_pessoal",
                    "agentes_chamados": state["agentes_chamados"] + ["agenda"]}
        filters = _filtros_treinamentos(state["mensagem"])
        event_filters = _filtros_eventos(state["mensagem"])
        if isinstance(prepared_decision, AgendaToolDecision):
            decision = prepared_decision
        elif filters is not None:
            decision = AgendaToolDecision(acao="consultar_treinamentos", filtros=filters)
        elif event_filters is not None:
            decision = AgendaToolDecision(acao="consultar_eventos", filtros=event_filters)
        else:
            decision = await invoke_agent(
                model, "agenda", AGENDA_PROMPT_COMPLETO, state, AgendaToolDecision,
            )
        if decision.acao == "responder":
            return {
                "agenda_route": "fim",
                "resultado": decision.resposta.model_dump(exclude_none=True),
                "agentes_chamados": state["agentes_chamados"] + ["agenda"],
            }
        return {
            "agenda_route": "tool",
            "agenda_decision": decision,
            "agentes_chamados": state["agentes_chamados"] + ["agenda"],
        }

    async def use_tool(state: ChatState):
        if state["agenda_decision"] == "grafico_pessoal":
            normalized = "".join(c for c in unicodedata.normalize("NFKD", state["mensagem"].casefold()) if not unicodedata.combining(c))
            chart_type = "grafico_linhas" if re.search(r"\b(linhas?|evolucao|mensal)\b", normalized) else "grafico_rosca" if re.search(r"\b(rosca|donut)\b", normalized) else "grafico_setores" if re.search(r"\b(setores?|pizza|circular)\b", normalized) else "grafico_barras"
            tool_name = "consultar_evolucao_treinamentos" if chart_type == "grafico_linhas" else "consultar_grafico_treinamentos"
            result = await AGENDA_TOOLS[tool_name].ainvoke(
                {}, config={"configurable": {"usuario_atual": state["usuario_atual"].model_dump()}})
            result = {**result, "tipo_grafico": chart_type}
            categories = result.get("categorias", {})
            total = sum(categories.values())
            prefix = "Suas conclusões de treinamentos em eventos ativos por mês, nos últimos 12 meses: " if chart_type == "grafico_linhas" else "Seus treinamentos atribuídos em eventos ativos por situação: "
            candidate = (prefix +
                         ", ".join(f"{name}: {count}" for name, count in categories.items()) +
                         f". Total: {total}.") if result.get("status") in {"ok", "sem_dados"} else result.get("mensagem", "Consulta indisponível.")
            return {"resultado_tool": result, "resposta_deterministica": True, "candidato": candidate,
                    "resultado": {"dominio": "agenda", "intencao": "consultar", "status": "concluido" if result.get("status") == "ok" else "sem_dados" if result.get("status") == "sem_dados" else "indisponivel", "resposta": candidate, "recomendacao": "", "apresentacao": presentation_evidence(tool_name,result), "evidencia_tool": {"nome": tool_name, "resultado": result}},
                    "acao_pendente": None, "agentes_chamados": state["agentes_chamados"] + [tool_name]}
        filters = state["agenda_decision"].filtros
        decision = state["agenda_decision"]
        tool_name = decision.acao
        result = await AGENDA_TOOLS[tool_name].ainvoke(
            filters.model_dump(),
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        tool_status = result.get("status")
        statuses = {
            "ok": "concluido", "sem_dados": "sem_dados", "sem_perfil": "sem_dados",
            "indisponivel": "indisponivel", "erro": "nao_autorizado",
            "nao_aplicavel": "nao_autorizado",
        }
        if tool_name == "consultar_eventos":
            selection=bool(re.search(r'\b(refer[eê]ncia|evento_id)\b',state['mensagem'],re.I))
            candidate = _referencias_eventos(result) if selection else _formatar_eventos(result)
            evidence = _evidencia_treinamentos({**result, "treinamentos": result.get("eventos", [])})
            evidence["eventos"] = evidence.pop("treinamentos", [])
            if selection:
                evidence['eventos']=[{k:item[k] for k in ('evento_id','titulo') if k in item} for item in evidence['eventos']]
            evidence["proximos"] = result.get("proximos")
            intention = "listar"
        elif tool_name == "consultar_treinamentos":
            candidate = _formatar_treinamentos(result)
            evidence = _evidencia_treinamentos(result)
            intention = "consultar"
        return {
            "resultado_tool": result,
            "resposta_deterministica": True,
            "resultado": {
                "dominio": "agenda",
                "intencao": intention,
                "status": statuses.get(tool_status, "indisponivel"),
                "resposta": "Operação de Agenda executada por ferramenta autorizada.",
                "recomendacao": "",
                "evidencia_tool": {
                    "nome": tool_name,
                    "resultado": evidence,
                },
                "apresentacao":presentation_evidence(tool_name,result),
            },
            "candidato": candidate,
            "acao_pendente": None,
            "eventos_referencias": [{"evento_id":item["evento_id"],"titulo":item["titulo"]}
                                    for item in result.get("eventos",result.get("treinamentos",[]))
                                    if type(item.get("evento_id")) is int and isinstance(item.get("titulo"),str)][:50],
            "agentes_chamados": state["agentes_chamados"] + [tool_name],
        }

    graph = StateGraph(ChatState)
    graph.add_node("decidir", decide)
    graph.add_node("usar_tool_agenda", use_tool)
    graph.add_edge(START, "decidir")
    graph.add_conditional_edges("decidir", lambda state: state["agenda_route"], {
        "tool": "usar_tool_agenda", "fim": END,
    })
    graph.add_edge("usar_tool_agenda", END)
    return graph.compile(name="subgrafo_agenda")


def build_faq_graph(model: AgentModel, search_faq=None):
    async def consult_norms(state: ChatState):
        public_requested = _pedido_fontes_publicas_faq(state["mensagem"])
        jobs = []
        if search_faq is not None:
            from app.modules.chat.session_context import faq_search_query
            jobs.append(("faq", search_faq(faq_search_query(state))))
        if public_requested:
            jobs.append(("publico", consultar_fontes_publicas(
                state["mensagem"], area="faq_politicas",
            )))
        responses = await asyncio.gather(
            *(job for _, job in jobs), return_exceptions=True,
        ) if jobs else []
        resolved = dict(zip((name for name, _ in jobs), responses))

        faq_response = resolved.get("faq", [])
        faq_error = faq_response if isinstance(faq_response, BaseException) else None
        snippets = [] if faq_error else faq_response
        public_response = resolved.get("publico", {
            "status": "nao_consultado", "fontes": [],
        })
        if isinstance(public_response, BaseException):
            public_response = {"status": "indisponivel", "fontes": []}
        public_sources = public_response.get("fontes", [])
        if faq_error and not public_sources:
            raise faq_error

        found = bool(snippets or public_sources)
        unavailable = search_faq is None and (
            not public_requested or public_response.get("status") == "indisponivel"
        )
        result = {
            "dominio": "faq",
            "status": "encontrado" if found else "indisponivel" if unavailable else "sem_dados",
            "trechos": snippets,
            "fontes_publicas": public_sources,
            "consulta_publica": public_response.get("status"),
        }
        called = state["agentes_chamados"] + ["consultar_normas"]
        if public_requested:
            called.append("consultar_fontes_publicas")
        return {
            "resultado": result,
            "agentes_chamados": called,
        }

    async def answer(state: ChatState):
        status = state["resultado"]["status"]
        if status == "indisponivel":
            response = ("A consulta às normas está indisponível no momento. "
                        "Confirme sua dúvida com a área responsável.")
        elif status == "sem_dados":
            response = "Não encontrei essa informação nas normas disponibilizadas ao Astro."
        else:
            response = await invoke_agent(model, "faq", FAQ_PROMPT_COMPLETO, state)
        if response == ROUTER_CLARIFICATION:
            return {
                "candidato": response, "resposta_deterministica": True,
                "resultado": {**state["resultado"], "status": "esclarecer"},
                "agentes_chamados": state["agentes_chamados"] + ["faq"],
            }
        return {
            "candidato": response,
            "agentes_chamados": state["agentes_chamados"] + ["faq"],
        }

    graph = StateGraph(ChatState)
    graph.add_node("consultar_normas", consult_norms)
    graph.add_node("responder", answer)
    graph.add_edge(START, "consultar_normas")
    graph.add_edge("consultar_normas", "responder")
    graph.add_edge("responder", END)
    return graph.compile(name="subgrafo_faq")

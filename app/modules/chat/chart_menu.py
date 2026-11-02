"""Opções de gráficos locais à sessão; a seleção nunca substitui autorização."""
import re
import unicodedata

from app.modules.chat.capabilities import profile_capabilities
from app.modules.operational.tools import OperationalDecision


OPTIONS = {
    "pendencias_nr": ("Pendências de NR da equipe — barras, setores ou rosca", "consultar_grafico_pendencias_nr"),
    "vencimentos": ("Vencimentos da equipe por faixa de prazo — barras, setores ou rosca", "consultar_grafico_vencimentos"),
    "cobertura": ("Indicadores de cobertura da equipe — barras ou setores", "consultar_indicadores_cobertura"),
    "cargos": ("Distribuição da equipe por cargo — barras, setores ou rosca", "consultar_indicadores_equipe"),
    "conclusoes_pessoais": ("Minhas conclusões de treinamentos por mês — linhas", None),
    "treinamentos_pessoais": ("Meus treinamentos por situação — barras, setores ou rosca", None),
}


def normalize(text: str) -> str:
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                           if not unicodedata.combining(c)).split()).strip().rstrip('.!? ')


def asks_chart_menu(message: str) -> bool:
    return normalize(message) in {
        "quais graficos vc pode fazer", "quais graficos voce pode fazer", "quais graficos voce consegue fazer",
        "quais graficos posso pedir", "quais graficos estao disponiveis", "mostre as opcoes de graficos",
        "que graficos voce faz", "quais tipos de graficos voce faz",
        "me faca um grafico", "faca um grafico", "faz um grafico", "queria visualizar um grafico",
        "quero ver um dash", "quais graficos vc conswfue fazer",
    }


def allowed_options(role: str) -> list[str]:
    if role not in {"GESTOR", "GESTOR_WORKSPACE", "COLABORADOR"}:
        return []
    personal = ["conclusoes_pessoais", "treinamentos_pessoais"]
    return list(OPTIONS) if role in {"GESTOR", "GESTOR_WORKSPACE"} else personal


def make_chart_menu(role: str) -> tuple[str, dict | None]:
    options = allowed_options(role)
    if not options:
        return ("Seu perfil de Administrador não possui consultas funcionais para esses gráficos. "
                "Posso explicar as funções do Astro e consultar seu cadastro.", None)
    label = profile_capabilities(role)["perfil"]
    text = f"No seu perfil de {label}, você pode pedir:\n\n"
    text += '\n'.join(f"{i}. {OPTIONS[key][0]}." for i, key in enumerate(options, 1))
    text += ("\n\nResponda com o número ou o nome da opção para eu consultar e montar o gráfico. "
             "Nas comparações sem formato escolhido, uso barras. Cada consulta verifica seu acesso "
             "e os dados disponíveis. O gráfico de vencimentos usa faixas fixas, incluindo vencidas e sem validade.")
    return text, {"versao": 1, "opcoes": options}


def select_chart(message: str, menu: dict | None, role: str) -> OperationalDecision | str | None:
    text = normalize(message)
    if not isinstance(menu, dict) or menu.get("versao") != 1:
        return None
    options = menu.get("opcoes")
    if (not isinstance(options, list) or not options or len(options) > len(OPTIONS)
            or any(not isinstance(key, str) or key not in OPTIONS for key in options)
            or len(set(options)) != len(options)):
        return "Não consegui recuperar as opções anteriores. Pergunte novamente quais gráficos estão disponíveis."
    number = re.fullmatch(r"(?:(?:opcao|numero|quero a opcao) )?(\d{1,2})", text)
    ordinal = {"o primeiro": 1, "o segundo": 2, "o terceiro": 3, "a primeira": 1, "a segunda": 2}.get(text)
    if ordinal:
        number = re.fullmatch(r"(\d{1,2})", str(ordinal))
    if text in {"qualquer um", "qualquer um q vc conseguir", "o que voce quiser", "faz um ai", "faca um desses", "faz algum desses"}:
        number = re.fullmatch(r"(\d{1,2})", "1")
    names = {"pendencias de nr da equipe": "pendencias_nr", "pendencias de nr": "pendencias_nr",
             "pendencias": "pendencias_nr", "faca isso, quero um grafico das pendencias": "pendencias_nr",
             "vencimento": "vencimentos", "venximento": "vencimentos",
             "vencimentos": "vencimentos", "cobertura": "cobertura", "por cargo": "cargos",
             "minhas conclusoes": "conclusoes_pessoais", "meus treinamentos": "treinamentos_pessoais"}
    selected = (options[int(number[1]) - 1] if number and 1 <= int(number[1]) <= len(options)
                else names.get(text))
    if selected is None:
        if number or text in {"s", "sim", "faca isso"}:
            return f"Escolha uma opção de 1 a {len(options)} ou diga o nome do gráfico."
        return None
    if selected not in options or selected not in allowed_options(role):
        return "Essa opção não está disponível para seu perfil atual. Pergunte novamente quais gráficos você pode consultar."
    if selected in {"conclusoes_pessoais", "treinamentos_pessoais"}:
        return "grafico_linhas" if selected == "conclusoes_pessoais" else "grafico_barras"
    return OperationalDecision(acao=OPTIONS[selected][1], filtros={"tipo_grafico": "grafico_barras", "limite": 50})


def expiry_followup(message: str, pending: bool, role: str) -> tuple[OperationalDecision | str, bool] | None:
    """Resolve somente um esclarecimento de prazo emitido pela aplicação nesta sessão."""
    text = normalize(message)
    if role not in {"GESTOR", "GESTOR_WORKSPACE"}:
        return None
    if pending is True:
        match = re.fullmatch(r"(?:nos proximos |proximos )?(\d{1,3})\s*(?:d|dias?)", text)
        if match:
            days = int(match[1])
            if not 1 <= days <= 365:
                return "Informe um período entre 1 e 365 dias.", True
            return OperationalDecision(acao="consultar_vencimentos_conformidades", filtros={"dias": days, "limite": 50}), False
    if text in {"vencimento", "vencimentos", "venximento"}:
        return "Para qual período você quer consultar os vencimentos da equipe? Por exemplo, próximos 30 dias.", True
    return None

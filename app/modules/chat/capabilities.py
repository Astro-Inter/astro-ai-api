"""Orientação pública por papel; não concede acesso nem substitui as tools."""
import re
import unicodedata

from app.core.security import AccessRole


VISUAL_CAPABILITIES = (
    "Posso apresentar gráficos de barras, linhas, setores e rosca, tabelas e fichas de perfil, "
    "quando a consulta permitir e houver dados autorizados. Fotos aparecem quando disponíveis. "
    "Cada tipo de gráfico depende do que os dados permitem comparar."
)
PDF_CAPABILITIES = (
    "Também posso gerar um PDF para baixar com o conteúdo consultado e aprovado. "
    "Por exemplo: “Gere um PDF desse resumo”."
)
PERSONAL_EXAMPLES = (
    "Tenho pendências de NR?",
    "Quais treinamentos eu preciso realizar?",
    "Mostre meus treinamentos concluídos em um gráfico de linhas.",
    "Quais são meus próximos eventos?",
)
TEAM_EXAMPLES = (
    "Quem da minha equipe tem pendências de NR?",
    "Quais conformidades vencem nos próximos 30 dias?",
    "Mostre a distribuição da minha equipe por cargo em um gráfico de barras.",
    "Mostre indicadores de cobertura da minha equipe.",
    "Mostre 100 colaboradores da minha equipe em uma tabela.",
)
PROFILE_GUIDES = {
    "COLABORADOR": ("Colaborador", "seus próprios dados e atividades",
                    PERSONAL_EXAMPLES, "Consultas de outras pessoas e indicadores de equipe não estão disponíveis para esse perfil."),
    "GESTOR": ("Gestor", "sua unidade, dentro dos dados autorizados",
               TEAM_EXAMPLES, "Você também pode consultar seus próprios dados, pendências e treinamentos."),
    "GESTOR_WORKSPACE": ("Gestor do Workspace", "as unidades do seu workspace, dentro dos dados autorizados",
                         TEAM_EXAMPLES + ("Compare a conformidade entre as unidades.",),
                         "Você também pode consultar seus próprios dados, pendências e treinamentos."),
    "ADMIN": ("Administrador", "seu cadastro administrativo e informações gerais do Astro",
              ("Meus dados.", "Como funciona o Astro?", "O que é uma NR?"),
              "Esse perfil não possui cadastro funcional para consultas pessoais de NRs e treinamentos; não equivale a Gestor do Workspace."),
}


def profile_capabilities(role: AccessRole) -> dict:
    label, scope, examples, note = PROFILE_GUIDES[role]
    return {
        "perfil": label, "escopo": scope, "exemplos": list(examples), "observacao": note,
        "recursos_gerais": (
            "Posso explicar o Astro e NRs e mostrar a ficha do seu cadastro. "
            + ("Gráficos de equipe e consultas funcionais exigem um perfil autorizado. "
               if role == "ADMIN" else VISUAL_CAPABILITIES + " ")
            + PDF_CAPABILITIES
        ),
        "limites": "Cada consulta verifica seu acesso e os dados disponíveis. O chatbot não altera seu perfil, não inscreve pessoas em treinamentos e não cria eventos.",
    }


def asks_profile_capabilities(message: str) -> bool:
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold())
                   if not unicodedata.combining(c))
    text = re.sub(r'\s+', ' ', text).strip().rstrip('.!? ').strip()
    text = re.sub(r',? por favor$', '', text).strip()
    return text in {
        "que tipo de perguntas o meu perfil pode fazer", "que tipo de perguntas meu perfil pode fazer",
        "que perguntas posso fazer com meu perfil", "quais perguntas posso fazer com meu perfil",
        "o que posso fazer com meu perfil", "o que posso fazer com o meu perfil",
        "o que meu perfil pode fazer", "o que posso consultar com meu perfil",
        "o que posso fazer com meu nivel de acesso", "o que posso consultar com meu nivel de acesso",
        "quais sao as funcionalidades disponiveis para meu perfil",
        "quais sao as funcionalidades disponiveis para o meu perfil",
    }


def format_profile_capabilities(capabilities: dict) -> str:
    return "\n\n".join([
        f"Seu perfil é {capabilities['perfil']}. Posso ajudar com consultas sobre {capabilities['escopo']}.",
        "Você pode perguntar, por exemplo:\n" + "\n".join(f"- {question}" for question in capabilities['exemplos']),
        capabilities["observacao"], capabilities["recursos_gerais"], capabilities["limites"],
    ])

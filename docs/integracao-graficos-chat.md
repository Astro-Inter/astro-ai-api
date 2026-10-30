# Gráficos no chat: contrato para o frontend

A primeira versão entrega um **gráfico de barras das participações do próprio
usuário em treinamentos de eventos ativos**, separado por situação. Não é um
indicador da empresa, nem um painel de conformidade ou de treinamentos vencidos.
Exemplo de pedido: “Mostre um gráfico dos meus treinamentos”.

`POST /chat/messages` mantém `session_id`, `resposta` e `agentes_chamados` e
acrescenta `blocos`, uma lista ordenada. Quando vazia, renderize `resposta` como
antes. Quando preenchida, percorra os blocos na ordem recebida; `resposta` é o
fallback textual e não deve ser mostrado uma segunda vez. O mesmo campo opcional
está em cada item de `GET /sessions/{session_id}/messages`. Mensagens antigas
possuem lista vazia. O histórico mantém o retrato retornado no momento da consulta;
atualizar os dados exige um novo pedido no chat.

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "resposta": "Seus treinamentos atribuídos em eventos ativos por situação: Pendentes: 3, Concluídos: 2, Rejeitados: 1. Total: 6.",
  "agentes_chamados": ["guardrail_entrada", "roteador", "agenda", "consultar_grafico_treinamentos", "juiz", "guardrail_saida"],
  "blocos": [
    {"tipo": "texto", "conteudo": "Veja a distribuição dos seus treinamentos atribuídos em eventos ativos:"},
    {"tipo": "grafico_barras", "versao": 1, "titulo": "Seus treinamentos por situação", "dados": [{"categoria": "Pendentes", "valor": 3}, {"categoria": "Concluídos", "valor": 2}, {"categoria": "Rejeitados", "valor": 1}], "unidade": "participações", "fonte": "Participações em treinamentos ativos atribuídas ao usuário"},
    {"tipo": "texto", "conteudo": "Seus treinamentos atribuídos em eventos ativos por situação: Pendentes: 3, Concluídos: 2, Rejeitados: 1. Total: 6."}
  ]
}
```

Valores do exemplo são fictícios. Cada valor é inteiro não negativo; há até 10
pontos e 10 blocos. O texto pode conter Markdown conforme o parâmetro existente
`markdown`. O frontend escolhe sua biblioteca visual e trata texto como conteúdo,
não como HTML executável. Para tipo ou versão desconhecidos, renderize o fallback
`resposta`; não execute código nem busque URLs indicadas pelos dados. Disponibilize
uma alternativa textual/tabela e o título e fonte para leitores de tela.

A tool LangChain usa o UID do contexto autenticado e uma conexão somente leitura.
Conta participações atribuídas ao usuário via `turma_colaborador`, em eventos
`ATIVO`, agrupadas pelo status de `conclusao_evento`; sem conclusão, usa PENDENTE.
Pendentes incluem registros pendentes de validação, e não significam vencidos.
Valores vêm do banco, não do modelo. Identificadores de outros usuários não são
aceitos como filtros. O resultado é apresentado como candidato determinístico no
subgrafo Agenda e segue pelo Juiz e guardrail. Blocos só são enviados quando há
dados e a resposta final corresponde à candidata com Juiz aprovado. Sem dados,
falha ou acesso indisponível, retorna texto e `blocos: []`. O fluxo existente tem
fallback do Juiz para respostas determinísticas quando o provedor falha.

Escopo atual: um gráfico de barras pessoal. Filtros por unidade, empresa, período,
status e outros tipos de gráfico ainda não foram implementados. Não invente essas
opções no frontend. A API de dashboards da organização usa agregações Databricks e
regras próprias de workspace; este gráfico consulta PostgreSQL operacional e os
números não devem ser comparados como se tivessem a mesma definição.

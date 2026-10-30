# Gráficos no chat: contrato para o frontend

Os gráficos são pessoais e usam participações em treinamentos de eventos ativos.
Barras e setores mostram a distribuição por situação; linhas mostra a evolução
mensal das conclusões. Não são indicadores da empresa, conformidade ou vencimentos.

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

Valores do exemplo são fictícios. Cada valor é inteiro não negativo; há até 12
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

Escopo atual: três tipos de gráfico pessoal. Filtros por unidade, empresa, período
personalizado e status ainda não foram implementados. Não invente essas
opções no frontend. A API de dashboards da organização usa agregações Databricks e
regras próprias de workspace; este gráfico consulta PostgreSQL operacional e os
números não devem ser comparados como se tivessem a mesma definição.


## Tipos de gráficos

- `grafico_barras`: pedidos como “Mostre um gráfico dos meus treinamentos”. Mostra
  contagens por situação: Pendentes, Concluídos e Rejeitados.
- `grafico_setores`: pedidos como “Mostre um gráfico de setores dos meus treinamentos”
  ou “gráfico de pizza dos meus treinamentos”. Usa os mesmos pontos das barras;
  o frontend calcula as proporções pelo total e pode apresentá-las em porcentagem.
  Se não houver participações, o backend envia apenas texto e lista de blocos vazia.
- `grafico_linhas`: pedidos como “Mostre um gráfico de linhas dos meus treinamentos
  concluídos”. Mostra a quantidade de conclusões por mês nos **últimos 12 meses
  civis**, incluindo o mês atual. A data considerada é `data_conclusao`, com status
  `CONCLUIDO`, e a participação precisa pertencer ao usuário e a um evento ativo.
  Meses sem conclusões aparecem com valor zero. Conclusões sem data não entram.

Cada tipo preserva a mesma estrutura do exemplo acima, mudando `tipo`, título,
fonte e dados. Em linhas, a `categoria` é `AAAA-MM`, em ordem cronológica, e o
`valor` é a quantidade de participações concluídas naquele mês. Exemplo de bloco:

```json
{"tipo":"grafico_linhas","versao":1,"titulo":"Suas conclusões por mês",
 "dados":[{"categoria":"2026-01","valor":0},{"categoria":"2026-02","valor":2}],
 "unidade":"participações","fonte":"Conclusões pessoais de treinamentos em eventos ativos"}
```

O exemplo acima abrevia a série; uma resposta efetiva contém 12 pontos. Para
setores, use `"tipo":"grafico_setores"` com os mesmos campos e categorias do
gráfico de barras. O cliente deve apresentar uma tabela ou descrição acessível
para todos os tipos. A fonte e o escopo explicitam que não há comparação direta
com os gráficos organizacionais da API de dashboards.


## Tabela de usuários e fotos de perfil

Quando uma consulta autorizada de RH lista usuários, o chat pode entregar blocos
`texto`, `tabela`, `galeria_usuarios`, `texto`, nessa ordem quando ambos são
solicitados. A tabela e a galeria são blocos independentes; um não precisa estar
dentro do outro. Se pedir apenas tabela, a resposta pode conter só a tabela; se
pedir apenas fotos, só a galeria. Uma listagem sem preferência apresenta ambos. O contrato versionado é:

```json
{"tipo":"tabela","versao":1,"titulo":"Usuários","pagina":1,
 "total_paginas":1,"total":1,
 "colunas":[{"chave":"nome","titulo":"Nome"},{"chave":"email","titulo":"E-mail"}],
 "linhas":[{"nome":"Ana Exemplo","email":"ana@example.com"}]}
```

O exemplo de tabela foi abreviado: a resposta de usuários também traz cargo,
unidade, perfil e situação. As colunas definem as chaves de cada linha, com
valores textuais, numéricos ou nulos. Cada página contém no máximo 50 linhas.
As imagens chegam em bloco separado:

```json
{"tipo":"galeria_usuarios","versao":1,"titulo":"Fotos dos usuários",
 "imagens":[{"nome":"Ana Exemplo","email":"ana@example.com","url":null}]}
```

Cada item `url` é uma URL HTTPS assinada e temporária ou `null` quando não há
foto, autorização ou acesso ao serviço. O
frontend deve exibir nome e iniciais quando faltar foto ou ocorrer erro no
carregamento; não registre ou analise a URL nem a persista em cache local. Exiba
campos como texto, preserve paginação e leia as linhas como tabela acessível.
O campo `resposta` permanece o fallback para clientes que não renderizam blocos.
As URLs não são armazenadas no histórico nem enviadas aos modelos. Ao abrir o
histórico, a API revalida as permissões atuais e obtém novamente as URLs; se o
usuário deixar de ter acesso à foto, esse campo retorna `null`.

O fluxo reutiliza a consulta autorizada `buscar_outros_usuarios`. Depois do Juiz e
antes de enviar ao cliente, o backend revalida cada pessoa no PostgreSQL com o
UID e o papel atuais, limita o escopo à unidade do gestor ou workspace do gestor
do workspace, consulta `usuario_foto_perfil` e somente então acessa o Redis.
Administradores e colaboradores recebem fallback sem foto nesta implementação.
Nunca exponha chaves R2, caminho do objeto ou chaves Redis ao modelo/cliente.

O Redis usa a mesma chave `user:avatar:<id>` da API principal. Uma URL válida é
reutilizada apenas para o mesmo endpoint, bucket e objeto, dentro da validade; na
ausência ou expiração, o backend gera URL de até 24h e atualiza o cache com TTL
inferior ao da assinatura. Falha do Redis não impede geração; ausência de dados,
credenciais ou erro de autorização retorna `null`. A foto é acessível por quem
possuir a URL até sua expiração, portanto somente entregue a usuários autorizados.

Configuração exclusivamente no backend: `AVATAR_REDIS_URL`, `AVATAR_R2_ENDPOINT`,
`AVATAR_R2_ACCESS_KEY_ID`, `AVATAR_R2_SECRET_ACCESS_KEY` e `R2_BUCKET_AVATARS`.
O endpoint deve ser HTTPS de Cloudflare R2. Configure Redis e bucket iguais aos da
API principal para compartilhar o cache; os segredos de PDF têm nomes separados.
Use `.env.example` como referência e nunca versione valores reais. Testes locais
usam serviços simulados; conexão e permissões reais exigem homologação própria.

# Gráficos no chat: contrato para o frontend

Os gráficos de treinamentos são pessoais e usam participações em eventos ativos.
Gráficos de equipe são produzidos por `consultar_indicadores_equipe`; consulte
[Tools operacionais e autorização](tools-operacionais.md).
Barras e setores mostram a distribuição por situação; linhas mostra a evolução
mensal das conclusões. Não são indicadores da empresa, conformidade ou vencimentos.

`POST /chat/messages` retorna `session_id`, `agentes_chamados` e `blocos`, uma lista ordenada e não vazia. O campo `resposta` não é retornado: até respostas apenas textuais chegam em um bloco `texto`. Percorra os blocos na ordem recebida. Clientes devem ser atualizados antes da API, pois versões antigas dependem de `resposta`. O mesmo campo opcional
está em cada item de `GET /sessions/{session_id}/messages`. Mensagens antigas
possuem lista vazia. O histórico mantém o retrato retornado no momento da consulta;
atualizar os dados exige um novo pedido no chat.

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
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
não como HTML executável. Para tipo ou versão desconhecidos, apresente os blocos de texto disponíveis e informe que parte da resposta não pôde ser exibida; não execute código nem busque URLs indicadas pelos dados. Disponibilize
uma alternativa textual/tabela e o título e fonte para leitores de tela.

A tool LangChain usa o UID do contexto autenticado e uma conexão somente leitura.
Conta participações atribuídas ao usuário via `turma_colaborador`, em eventos
`ATIVO`, agrupadas pelo status de `conclusao_evento`; sem conclusão, usa PENDENTE.
Pendentes incluem registros pendentes de validação, e não significam vencidos.
Valores vêm do banco, não do modelo. Identificadores de outros usuários não são
aceitos como filtros. O resultado é apresentado como candidato determinístico no
subgrafo Agenda e segue pelo Juiz e guardrail. Blocos só são enviados quando há
dados e a resposta final corresponde à candidata com Juiz aprovado. Sem dados,
falha ou acesso indisponível, retorna um bloco de texto. O fluxo existente tem
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
  Se não houver participações, o backend envia um bloco de texto.
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

Quando uma consulta autorizada de RH lista usuários, a resposta apresenta blocos
`texto`, `tabela`, `texto`. A foto fica na própria linha da pessoa, identificada
por uma célula de tipo `imagem`; não é gerado um bloco de galeria.

```json
{"tipo":"tabela","versao":1,"titulo":"Usuários","pagina":1,
 "total_paginas":1,"total":1,
 "colunas":[{"chave":"foto","titulo":"Foto"},{"chave":"nome","titulo":"Nome"},{"chave":"email","titulo":"E-mail"}],
 "linhas":[{"foto":{"tipo":"imagem","url":null,"descricao":"Foto de Ana Exemplo"},"nome":"Ana Exemplo","email":"ana@example.com"}]}
```

A tabela real inclui também cargo, unidade, perfil e situação. A URL é HTTPS,
assinada e temporária, ou `null` quando a foto não está disponível ou autorizada.
O frontend deve renderizar a célula `imagem` visualmente, usar sua descrição
para acessibilidade e apresentar uma alternativa quando faltar a foto. Não
registre a URL nem a persista em cache local. Há no máximo 50 linhas por página.
O histórico conserva `content` para compatibilidade com mensagens antigas;
o POST não retorna `resposta`.
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

## Referências ao turno anterior

O roteador recebe o histórico recente dentro de um orçamento de caracteres.
Respostas longas são abreviadas preservando início e final com um aviso de trecho
omitido, em vez de remover o turno inteiro. Assim, referências como “nesses”
continuam vinculadas à listagem anterior, inclusive sua paginação. Uma lista
abreviada ou paginada não pode ser usada como se representasse todos os usuários.
Gráficos de funcionários usam `consultar_indicadores_equipe`, preservando os
filtros anteriores de RH. A agregação é feita sobre todo o conjunto autorizado,
nunca contando apenas a página ou o trecho de histórico exibido.

## Ficha individual (`ficha_usuario`, versão 1)

Pedidos como “Mostre a ficha de ana@example.com” ou “Qual a conformidade de Ana
Silva?” usam a consulta SST `consultar_conformidade_usuario`. A ficha reúne os
campos autorizados dessa consulta, após Juiz e guardrails. Nome ambíguo exige
esclarecimento; pessoa fora do escopo ou falha produz bloco de texto, sem ficha.
O escopo atual é de gestores consultando terceiros em sua unidade/workspace.
Campos ausentes são nulos e não devem ser inventados ou exibidos como vazios.

```json
{"tipo":"ficha_usuario","versao":1,"titulo":"Ficha do usuário",
 "nome":"Ana Exemplo","email":"ana@example.com","cargo":"Técnica",
 "unidade":"Matriz","perfil":null,"situacao":null,
 "foto":{"tipo":"imagem","url":null,"descricao":"Foto de Ana Exemplo"},
 "conformidade":{"percentual":66.67,"vigentes":2,"total":3,
   "data_referencia":"2026-10-30",
   "criterio":"NRs vigentes / NRs aplicáveis retornadas"}}
```

Exemplo fictício. Renderize como cartão com foto, campos identificados e coluna
ou seção de conformidade, adaptando o layout ao espaço disponível. O percentual
é calculado pela aplicação: `round(100 * vigentes / total, 2)`. Conta exclusivamente
as NRs retornadas pela consulta existente de cargo/unidade, não revogadas, com
status `VIGENTE` calculado com `CURRENT_DATE` do PostgreSQL. Zero NRs produz
`percentual: null`, com indicação “Sem NRs aplicáveis”, nunca 0% ou 100%.
Mostrar contagens, critério e data permite interpretar o indicador. Não é
certificação legal. A API Java consultada delega indicadores da home às funções
`fn_carrega_home_colaborador`, `fn_carrega_home_gestor` e `fn_carrega_workspace`;
sem a definição dessas funções não foi confirmada equivalência com esses painéis.

A foto usa a mesma resolução autorizada das tabelas: URL só é preenchida após
persistir a mensagem, revalidada no histórico, não enviada aos modelos nem salva
no Mongo. Frontends devem restringir a origem, oferecer imagem alternativa e não
persistir URLs. O cartão mantém os blocos de texto seguintes na ordem recebida.
Não adiciona chamadas de IA nem consultas de conformidade por pessoa em listagens.


## Gráficos de pendências e validade

Além de `participações` e `pessoas`, o campo `unidade` dos gráficos admite
`pendências` e `registros`. Frontends devem apresentar essa unidade, o título e
a fonte; não rotular todos os gráficos como quantidade de pessoas.

As tools `consultar_grafico_pendencias_nr` e `consultar_grafico_vencimentos`
produzem os mesmos blocos versionados `grafico_barras`/`grafico_setores`, com
`dados: [{"categoria":"NR-10","valor":4}]` ou categorias de prazo. O tipo visual
segue o pedido. A tabela acompanha o gráfico e contém as pessoas únicas por
categoria e a data de referência; essas contagens não devem ser somadas como
pessoas únicas globais. Regras e faixas estão em [Tools operacionais](tools-operacionais.md).
Não há série mensal nesses gráficos. Linhas permanecem disponíveis para as
conclusões pessoais de treinamentos, com a definição já documentada.

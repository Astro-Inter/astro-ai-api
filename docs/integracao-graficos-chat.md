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
registre a URL nem a persista em cache local. Na versão 1 há no máximo 50 linhas por página do servidor; novas listagens de usuários usam a versão 2 descrita abaixo.
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


## Tabelas de usuários com paginação local (versão 2)

`buscar_outros_usuarios.limite` agora expressa a quantidade solicitada: por exemplo,
100. O contrato aceita quantidades entre 1 e 1.000.000 para registrar pedidos acima do teto e informar o corte. O padrão é 20; o máximo efetivamente devolvido é 200. `pagina` é legado e
somente aceita 1. A API reúne lotes SQL internos de até 50, com os mesmos filtros,
autorização e ordenação por nome, email e ID único. Contagem e lotes usam uma
transação somente leitura com snapshot REPEATABLE READ. Não há chamadas adicionais
a modelos por lote. Falhas SQL abortam a consulta, sem sucesso parcial.

O bloco `tabela` de usuários usa `versao: 2` e `paginacao: "local"`:

```json
{
  "tipo": "tabela", "versao": 2, "titulo": "Usuários",
  "colunas": [{"chave": "nome", "titulo": "Nome"}],
  "linhas": [{"nome": "Pessoa autorizada"}],
  "pagina": 1, "total_paginas": 1, "total": 143,
  "solicitada": 1, "retornada": 1, "limitado": false,
  "tem_mais": true, "motivo_limite": null, "paginacao": "local"
}
```

`total` é a contagem autorizada pelos filtros; `retornada` é o número de linhas.
`tem_mais` indica que existem registros fora do conjunto recebido. `limitado`
indica que um limite de segurança impediu atender a quantidade solicitada e
disponível; seu motivo é `maximo_resultados` ou `tamanho_resposta`. Pedir 100 e
receber os 23 disponíveis não é corte de segurança. Pedir 100 e receber 100 de
143 é um conjunto parcial por escolha do solicitante, com `tem_mais: true`.
Os campos legados pagina/total_paginas valem 1 e não descrevem páginas visuais.

O orçamento da resposta de listagem é 2 MiB de JSON, incluindo reserva conservadora para fotos e
metadados. Cada célula textual tem até 500 caracteres e a URL de foto até 4096.
Se o próximo registro exceder o orçamento, a coleta para e informa o corte.
A resposta de listagem contém essa única tabela e textos curtos de resumo; não
repete todos os registros em texto, evitando duplicação e excesso de contexto.
As fotos continuam resolvidas no backend e não são persistidas no histórico.

Os gráficos agregados continuam em tools próprias: agregam todo o escopo filtrado
antes da paginação e não usam a amostra da tabela como população. O frontend não
deve calcular um gráfico global a partir dessas linhas.

### Publicação coordenada e histórico

DEPENDÊNCIA: os clientes web #70 e mobile #79 inspecionados validam até 50 linhas e ainda
não oferecem paginação local. Antes de publicar a API, atualizar ambos para ler
versões 1 e 2, validar até 200 linhas na v2 e mostrar, por exemplo, 10 ou 20 linhas
por página, sem nova chamada ao chatbot. Exibir contagem recebida/total e avisos
de corte; não renderizar todas as linhas de uma vez. Testar também imagens,
rolagem e troca de página. Esta alteração de backend não publica esses clientes.

Persistir o bloco completo recebido, incluindo versão e metadados. Ao reabrir a
conversa, reconstruir as páginas locais do conjunto salvo. Blocos v1 antigos
mantêm a semântica original de páginas do servidor e até 50 linhas; não migrar
suas contagens nem buscar automaticamente registros adicionais. Nenhuma consulta
ou foto histórica concede acesso novo: a autorização continua no backend.

O Juiz recebe metadados da consulta v2 e revisa o resumo; os registros não são enviados ao modelo nessa etapa. A aplicação valida o contrato e monta as linhas autorizadas diretamente, reduzindo custo e latência.


## Cartão do usuário autenticado (`buscar_meus_dados`)

Pedidos completos como “Meus dados” retornam, nesta ordem, um bloco de texto de
apresentação e um `ficha_usuario` v1. A consulta SQL identifica o usuário somente
pelo UID do contexto autenticado. ADMIN usa o próprio cadastro administrativo;
associações de cargo/unidade ausentes não escondem o cadastro funcional.

Extensões aditivas de v1: `modalidade` opcional e `origem` opcional com valor
`usuario_autenticado`. `nome` agora também pode ser nulo. Clientes devem aceitar
esses campos adicionais e não exigir nome; exibir “Meu perfil” e um avatar neutro
quando não houver nome para extrair iniciais. Os demais campos ausentes são nulos.

```json
[
  {"tipo":"texto","conteudo":"Estes são os seus dados:"},
  {"tipo":"ficha_usuario","versao":1,"titulo":"Meu perfil",
   "origem":"usuario_autenticado","nome":"Pessoa Exemplo",
   "email":"pessoa@example.com","perfil":"COLABORADOR",
   "cargo":"Analista","unidade":"Matriz","modalidade":"HIBRIDO",
   "foto":{"tipo":"imagem","url":null,"descricao":"Foto do usuário autenticado"},
   "situacao":null,"conformidade":null}
]
```

Não são expostos status cadastral, data de cadastro ou CPF nesse cartão. Não há
consulta automática de conformidade; o campo permanece nulo. Campos nulos não
produzem rótulos vazios ou percentual zero na UI. Pedidos específicos (“Qual é meu
cargo?”, nome, e-mail, unidade, perfil e modalidade) continuam somente em texto,
limitados ao campo solicitado; campo ausente recebe a indicação de indisponibilidade.

`origem` permite ao backend revalidar a foto pelo UID atual, sem usar nome/e-mail do
bloco como seletor de identidade. Para os perfis funcionais, foto própria exige
cadastro ativo com papel correspondente; ADMIN sem fonte de foto suportada recebe
URL nula. Reutiliza `user:avatar:<id>` no Redis após autorização e as mesmas URLs
assinadas/validação de host/objeto/validade. Foto ausente, cache/R2 indisponível ou
falha de autorização deixa URL nula: frontend mostra iniciais, se houver nome.
Não há chamadas extras de IA para foto ou conformidade.

O histórico conserva a ordem, a origem e os campos opcionais, sem persistir URL.
Ao carregar a conversa, o backend resolve novamente a foto autorizada. Fichas
antigas sem `origem` mantêm a resolução autorizada de terceiros existente.

Web/mobile: aceitar nome nulo, renderizar modalidade quando disponível, ignorar
`origem` na UI e omitir campos nulos. Manter iniciais/fallback de erro para fotos,
URLs temporárias sem cache durável e a ordem dos blocos. Os parsers atuais que
exigem nome precisam ser atualizados antes da publicação deste contrato estendido.


## Revisão e diagnóstico dos blocos

O Juiz recebe o plano de apresentação gerado pela aplicação junto da evidência, para avaliar o resumo e os blocos que serão anexados. A aprovação continua necessária; conversão para texto simples não remove tabelas ou gráficos aprovados. Veja [diagnóstico dos testes mobile](diagnostico-mobile-pr30.md) para causas confirmadas, fotos e publicação da revisão.


## Vencimentos: apresentação concisa

Nas novas respostas aprovadas de `consultar_grafico_vencimentos`, a ordem é
`tabela`, gráfico (quando disponível) e `texto` com contexto. Não há bloco de
introdução repetindo o título. A tabela usa “Detalhamento por faixa de prazo”;
o gráfico conserva o título da consulta. O rodapé informa fonte, unidade,
referência disponível, paginação/quantidade de faixas e os avisos interpretativos.
As colunas, valores e categorias permanecem iguais, inclusive pessoas únicas por
faixa e data de referência. Não some pessoas únicas entre faixas.

Se o conjunto estiver parcial ou não permitir gráfico, a resposta contém tabela e
rodapé, com fonte e aviso mesmo sem gráfico. Nenhuma data é inventada quando não
houver referência. A ordem dos blocos recebidos deve ser respeitada pelo cliente;
a presença de um texto introdutório não é requisito do contrato. O histórico de
novas respostas mantém exatamente esses blocos. Mensagens antigas não são reescritas.

Rolagem com teclado, rótulos amigáveis de perfis, espaçamento da ficha e escala
visual dos gráficos são responsabilidades de apresentação nos clientes. A API
preserva códigos como `GESTOR_WORKSPACE`, valores e regras de acesso; não altera
dados para compensar esses problemas visuais.

# Tools operacionais e autorização

Todas são consultas. A decisão do modelo seleciona tool/filtros, nunca autoriza o
acesso. Os papéis vêm de Firebase + lookup do backend; UID, papel e token não são
argumentos públicos de nenhuma tool. ADMIN não herda o papel de gestor.

## Novas agregações gráficas

`consultar_grafico_pendencias_nr` e `consultar_grafico_vencimentos` exigem
GESTOR ou GESTOR_WORKSPACE e reutilizam a população autorizada das tools SQL.
ADMIN e COLABORADOR são negados antes de acessar o banco. As duas aceitam
os mesmos filtros de RH, NR opcional, paginação e barras/setores. Pedidos de
linhas são esclarecidos: esses indicadores representam o cadastro atual.

- `consultar_grafico_pendencias_nr`: agrupa no SQL as pendências verificadas por
  NR antes de aplicar LIMIT/OFFSET. Inclui somente pessoas ativas, NRs não
  revogadas atribuídas por cargo/unidade e situações diferentes de VIGENTE.
  Retorna `quantidade` de pendências pessoa/NR e `pessoas_unicas` em cada NR.
  Uma pessoa pode ter pendências em várias NRs: não somar pessoas únicas entre
  categorias. A unidade do gráfico é `pendências`, não `pessoas`.
- `consultar_grafico_vencimentos`: usa registros aplicáveis da view de
  conformidade individual, com as faixas fixas abaixo, em ordem, incluindo zeros
  quando a consulta completa confirma que a faixa está vazia. Não consulta NRs
  sem qualquer registro de conformidade: ausência de registro não é a categoria
  “Sem validade”. Esta categoria contém registros existentes com data nula.
  A unidade do gráfico é `registros`. `pessoas_unicas` é por faixa, não global.

| Faixa | Critério com CURRENT_DATE do PostgreSQL |
|---|---|
| Vencidas | validade anterior a hoje |
| Hoje a 7 dias | validade entre hoje e hoje + 7, inclusive |
| 8 a 30 dias | validade maior que hoje + 7 e até hoje + 30 |
| Mais de 30 dias | validade posterior a hoje + 30 |
| Sem validade | registro aplicável com validade nula |

As faixas não se sobrepõem. `dias` diferente de 30 ou `incluir_vencidas=true`
nesse gráfico pede esclarecimento para não ignorar um pedido de corte diferente;
para listas com janela personalizada, usar `consultar_vencimentos_conformidades`.
Uma resposta vazia em pendências não vira gráfico zerado. Falha de consulta não
é convertida em valores zero. Gráficos só são enviados com todas as categorias
na página e até 12 pontos; caso contrário, usar tabela e aviso. A tabela traz
data de referência e contagens; `total` é o número de categorias, não pessoas.

Exemplos de perguntas:

- “Mostre um gráfico de barras das pendências da equipe por NR.”
- “Mostre as pendências dos colaboradores ativos por NR em setores.”
- “Mostre as conformidades vencidas e a vencer por faixa de prazo.”
- “Faça um gráfico de barras de vencimentos dos colaboradores da unidade Matriz.”
- “Compare os colaboradores ativos por cargo em um gráfico de barras.”

A terceira necessidade usa `consultar_indicadores_equipe`, sem uma tool duplicada:
`agrupar_por` cargo/unidade/perfil/situacao mais filtros de tipos, status, nome,
cargo e unidade. Referências ao resultado RH anterior preservam filtros;
“próxima página” mantém a consulta operacional e revalida o escopo. As duas novas
ações seguem o mesmo caminho de roteamento estruturado, Juiz, guardrail, blocos
visuais e histórico; sem rodadas extras de IA por pessoa ou categoria.

## Matriz de acesso

| Tool | COLABORADOR | GESTOR | GESTOR_WORKSPACE | ADMIN |
|---|---|---|---|---|
| consultar_indicadores_equipe | Negado | Unidade autorizada, outros gestores/colaboradores | Workspace autorizado, demais usuários funcionais | Negado |
| consultar_vencimentos_conformidades | Negado | Mesmo escopo de equipe | Mesmo workspace | Negado |
| consultar_pendencias_equipe | Negado | Mesmo escopo de equipe | Mesmo workspace | Negado |
| consultar_conformidade_unidade | Negado | Unidades autorizadas pela API Java | Unidades autorizadas do workspace | Negado |
| consultar_formularios_pendentes | Negado | Recebidos pelo próprio gestor | Recebidos pelo próprio gestor | Negado |
| consultar_respostas_formulario | Negado | Formulário concluído enviado/recebido por ele, autorizado pela API | Formulário concluído autorizado no workspace | Negado |
| consultar_detalhes_evento | Evento de que participa, via endpoint pessoal | Evento autorizado do workspace pela API Java | Evento autorizado do workspace pela API Java | Negado |
| consultar_candidatos_treinamento | Negado | Elegibilidade calculada pela API Java | Elegibilidade calculada pela API Java | Negado |

Os perfis desconhecidos e as requisições sem contexto autenticado são negados.
Nas três consultas SQL, o banco revalida o UID, papel e status ATIVO do solicitante,
workspace e unidades retornadas por `fn_seleciona_unidades`. Gestor comum é limitado
à própria unidade. Não retorna o próprio solicitante nem contas ADMIN na equipe. Filtros adicionais
de perfil, situação, nome, cargo e unidade só restringem esse escopo. Referências
a uma lista anterior de RH herdam seus filtros; não contam apenas a página exibida.
Pendências consideram funcionários ativos; indicadores podem incluir situações
inativas porque essa é uma das dimensões solicitáveis. Todas usam conexão somente
leitura, statement timeout existente e limite máximo de 50 linhas por página.

## Critérios e exemplos

- “Mostre a distribuição dos funcionários por cargo”: indicadores agrupam no SQL
  antes da paginação. `agrupar_por`: cargo, unidade, perfil ou situacao. Se todas
  as categorias couberem na página (até 12), devolve gráfico de barras e tabela.
  `tipo_grafico` aceita barras ou setores; pedido de linhas pede esclarecimento
  porque o cadastro atual não fornece série histórica mensal. Com mais categorias,
  devolve tabela paginada, sem gráfico parcial disfarçado de total. A unidade do gráfico é `pessoas`. Contagens são de outros usuários no escopo.
- “Quais conformidades vencem nos próximos 30 dias?”: `dias` de 1 a 365,
  `incluir_vencidas=false`, `nr` opcional. Usa `vw_conformidade_colaborador`,
  registros aplicáveis e `CURRENT_DATE`. Limites incluem hoje e o último dia;
  datas nulas são excluídas, nunca interpretadas como vencidas. Com
  `incluir_vencidas=true`, acrescenta vencidas. Dias restantes podem ser negativos.
- “Quem precisa realizar ou renovar a NR-10?”: cruza cargo_nr, unidade_nr e NRs não
  revogadas. Reutiliza os critérios da consulta SST individual: maior validade
  aplicável (nulos por último), maior ID em empate; vigente tem precedência;
  depois atividade pendente em evento não cancelado; depois renovar ou realizar.
  Não calcula aptidão nem certifica conformidade legal. Não faz uma consulta por pessoa.
- “Como estão as conformidades da unidade Matriz?”: API `/conformities/units`,
  filtro opcional de nome exato da unidade e NR. Mostra validade, situação e origem.
  Não baixa anexos nem inventa evidências; origem é apenas o dado recebido da API.
- “Quais formulários ainda preciso responder?”: `/forms/received` com
  `status=PENDENTE`, paginação convertida de 1 no chat para 0 na API Java.
  A API atual só oferece esse recurso a gestores, não a colaboradores.
- “Mostre o preenchimento do formulário <referência>”: `/forms/{id}/responses`,
  apenas concluídos e autorizados. Resume quais perguntas foram respondidas,
  sem enviar respostas livres, geolocalização, imagens ou anexos aos modelos.
  Não é contagem de pessoas que responderam a um modelo de formulário: a API
  consultada representa uma instância entregue. Essa agregação ainda requer
  contrato adicional na API Java; não é inferida do número de perguntas.
- “Mostre os detalhes do evento <referência>”: endpoint `/events/{id}` para
  gestores, `/events/collaborator/{id}` para colaborador. Projeta somente campos
  públicos do evento e resumo das turmas; não retorna arrays de participantes,
  anexos ou URLs externas. Para colaborador, apenas os detalhes da participação.
- “Quem é elegível para a NR-10?”: `/events/eligible-candidates?nrId=10`.
  Com referência de evento, primeiro valida esse evento em `/events/{id}` e usa
  a NR vinculada; NR incompatível pede esclarecimento. Sem NR/evento, usa a
  consulta geral da API. Elegibilidade não garante vaga nem inscrição em um
  evento, e não contorna limites ou status de eventos.

## Contratos, fluxo e infraestrutura

Os argumentos são modelos Pydantic com `extra=forbid`, pagina >= 1 e limite 1–50.
Dias, NRs e identificadores têm limites próprios; URLs e caminhos não são aceitos
como identificadores. Não são aceitos SQL livre, UID, papel ou privilégios do modelo.

Pedidos operacionais reconhecidos usam uma decisão JSON `OperationalDecision`
via roteador Luna, em vez da classificação Jev que não gera argumentos. Uma
chamada de decisão substitui o roteamento habitual, sem uma rodada adicional de
IA para cada pessoa. As tools LangChain são executadas pela aplicação em um nó
LangGraph, com domínio RH/SST/Agenda; o resultado passa pelo Juiz e guardrail.
Após aprovação, o guardrail preserva o texto determinístico, como nas tools
existentes. Formulários ficam no domínio SST por envolver inspeções e evidências.

O resultado interno inclui status, fonte, colunas/linhas, total, pagina,
total_paginas e `completo`. O POST entrega blocos de texto, tabela e gráfico
quando aplicável. Paginação vazia não prova ausência de dados em outras páginas.
Uma falha produz texto de indisponibilidade/negação/esclarecimento, nunca dados
inventados ou um gráfico zerado. Histórico mantém o retrato da consulta;
para dados atuais é necessário consultar novamente. “Próxima página” imediatamente
após uma consulta operacional preserva os filtros e incrementa a página no backend,
revalidando o acesso. Mudanças de assunto não reaproveitam esse cursor.

Configuração: `ASTRO_API_BASE_URL=https://api.astro-app.solutions`, somente backend.
O gateway usa o Firebase ID token da requisição, já validado pela autenticação,
em um ContextVar temporário isolado por requisição, removido em `finally`. O token
não entra no estado do grafo, checkpoints, Mongo, prompts, resposta ou logs.
Não usa conta privilegiada de serviço. A API Java valida novamente o acesso.
A URL vem da configuração do servidor, não do modelo; exige HTTPS, sem credenciais
ou query. Cada chamada é GET, sem redirects, timeout de 12s, resposta até 2 MB,
sem retries automáticos. Candidatos de evento podem exigir dois GETs, sequenciais.
401/403, 404, 429, falhas HTTP, timeout e JSON inválido têm respostas sanitizadas.
400/422 pedem correção de filtros/referência. Formulários exigem UUID válido
antes de acessar a API Java.
Listas autorizadas completas da API Java são paginadas no gateway; acima do limite
de corpo o resultado é indisponível, não parcialmente contado.

## Fontes e validação pendente

Regras consultadas no código Java: EventController/EventService e
EventCandidateRepository; FormResponseController/FormAccessService; e
UnitConformityController/UnitConformityRepository. As primeiras três queries
usam o SQL existente de RH/SST e a view usada por ConformityRepository.
A documentação pública do banco não estava acessível pela ferramenta durante
a implementação. Os testes automatizados usam simulações. A validação real posterior está descrita
em [Homologação das tools](homologacao-tools-operacionais.md). Permanecem
necessárias validação visual e publicação coordenada dos contratos.


### Dados do próprio usuário

A ferramenta `buscar_meus_dados` e a resposta correspondente omitem o status cadastral e a data de cadastro (`criado_em`). Esses campos não são enviados ao modelo nessa consulta. As verificações internas de autorização e os filtros de situação das consultas de equipe permanecem preservados.

# Consultas de gestão de equipe no chatbot

As dez consultas são tools LangChain no domínio operacional existente do
LangGraph. Não são novos endpoints REST independentes: o cliente continua usando
`POST /chat/messages` e `GET /sessions/{session_id}/messages`. Roteador seleciona
uma ação estruturada; o backend autoriza a leitura; Juiz e guardrail revisam; o
serviço monta tabelas/gráficos validados e persiste os blocos na ordem entregue.
Não há aprovação, alteração, inscrição ou envio de mensagens nessas tools.

## Ações e perguntas

| Tool | Pergunta | Filtros específicos | Resultado |
| --- | --- | --- | --- |
| `consultar_matriz_conformidade_equipe` | Mostre a matriz de conformidade da equipe | nome, cargo, unidade, nr | Uma linha por pessoa/NR exigida, situação do registro, referência, validade, atividade agendada e envio em análise |
| `consultar_cobertura_treinamentos` | Quem precisa da NR-10 e não está em nenhum treinamento? | nome, cargo, unidade, nr, incluir_agendados | Lacunas sem atividade futura nem envio em análise por padrão; opção para também mostrar quem já tem encaminhamento |
| `consultar_evidencias_pendentes_validacao` | Quais evidências aguardam minha validação? | evento_id opcional | Referência da conclusão, evento, pessoa, turma, data registrada e quantidade de arquivos |
| `consultar_resultados_treinamento` | Como foi o treinamento selecionado? | evento_id obrigatório, tipo_grafico barras/setores | Contagens por situação de participação e pessoas únicas em cada situação, com gráfico quando completo |
| `consultar_evolucao_conclusoes_equipe` | Mostre a evolução mensal das conclusões da equipe | nome, cargo, unidade, nr, inicio, fim, tipo_grafico linhas/barras | Contagens mensais de conclusões registradas; gráfico de linhas por padrão |
| `consultar_conflitos_agenda` | Tenho treinamentos no mesmo horário? | escopo, nome, cargo, unidade, inicio, fim | Pares de turmas atribuídas com intervalo sobreposto |
| `simular_impacto_exigencia_nr` | Se exigirmos NR-35 neste cargo, qual o impacto? | nr e cargo ou unidade obrigatórios; tipo_grafico barras/setores | Distribuição dos registros atuais sob a hipótese, incluindo quantas pessoas já têm a exigência cadastrada |
| `consultar_indicadores_cobertura` | Mostre indicadores de cobertura da equipe | nome, cargo, unidade, nr | Cartões globais, tabela por NR e barras de lacunas sem encaminhamento |
| `comparar_conformidade_unidades` | Compare a conformidade entre unidades | nome, cargo, unidade, nr | Pessoas, pares aplicáveis/vigentes e percentual com denominador por unidade |
| `consultar_origem_conformidade` | De onde veio esta conformidade? | conformidade_id obrigatório, escopo, nome, cargo, unidade, nr | Origem cadastrada e referências de conclusão/formulário sem consultar seu conteúdo |

Referências numéricas são seletores de registros, não identidades autenticadas.
A matriz/cobertura retornam a referência da conformidade vigente/selecionada quando
ela existe; sem registro, a referência é nula. Uma referência não informada exige
esclarecimento para resultados de evento/origem, nunca seleção inventada pelo modelo.

## Acesso

| Consulta | Colaborador | Gestor | Gestor workspace | ADMIN |
| --- | --- | --- | --- | --- |
| Matriz, cobertura, indicadores, comparação e evolução | Não | Equipe autorizada da unidade | Equipe das unidades autorizadas do workspace | Não |
| Evidências pendentes | Não | Apenas eventos que criou | Apenas eventos que criou | Não |
| Resultados do evento | Não | Escopo de eventos da API Java | Escopo de eventos da API Java | Não |
| Conflitos e origem individual | Somente próprio cadastro | Próprio cadastro ou equipe autorizada | Próprio cadastro ou equipe autorizada | Não |
| Simulação | Não | Não | Equipe autorizada do próprio workspace | Não |

Consultas SQL revalidam no banco UID, perfil ativo e workspace. Equipe reutiliza
`fn_seleciona_unidades`, os tipos funcionais permitidos e a restrição de unidade do
gestor, sem incluir o próprio solicitante. Conflitos/origem usam `escopo=proprio`
por padrão para todos os perfis permitidos. `escopo=equipe` é opção de gestores.
Colaborador não pode enviar nome/cargo/unidade de terceiros ou selecionar equipe.

Resultados de evento fazem um GET fixo à Java com o token temporário do solicitante.
A Java autoriza eventos no workspace; isso pode diferir do recorte de equipe de uma
unidade. O token não entra no estado, nos prompts ou no histórico. A resposta de
participantes é agregada no backend; nomes, fotos e IDs de participantes não são
enviados ao modelo nessa consulta. Não se usa conta de serviço para ampliar acesso.

A fila de validação respeita a regra Java de ação do criador: somente evento ativo,
modo COLABORADOR, criado pelo gestor autenticado, e conclusão existente com status
PENDENTE. Uma conclusão PENDENTE significa envio em análise; ausência da conclusão
significa que ainda não foi enviada. A fila inclui envios com zero arquivos e
informa essa contagem; não cria link para download, abre, aprova ou rejeita arquivos.

## Critérios e limitações

- Matriz/cobertura usam usuários ativos e a interseção de `cargo_nr` e `unidade_nr`,
  excluindo NRs revogadas. NRs não aplicáveis são omitidas, sem transformar ausência
  de linha em “vigente”. O formato é pessoa/NR em tabela longa para não exceder o
  contrato de dez colunas; o novo bloco `matriz_conformidade` v1 identifica
  pessoa por referência estável e NR, permitindo apresentação específica no cliente.
- O registro aplicável mais recente por maior validade/ID define VIGENTE, VENCIDA,
  SEM_VALIDADE ou SEM_REGISTRO. Data nula não prova vigência. Atividade agendada
  exige evento ativo, turma com término ainda não passado e ausência de conclusão
  ou conclusão rejeitada. Envio PENDENTE é indicado separadamente como em análise.
- Cobertura exclui registros VIGENTES. Por padrão, exclui também atividade agendada
  e análise; `incluir_agendados=true` mantém essas linhas com seus indicadores.
  Trata-se de lacuna cadastral, sem certificação de conformidade legal ou aptidão.
- Resultados contam participações da resposta autorizada Java, não presença.
  Pessoa inscrita em várias turmas pode ter várias participações. Pessoas únicas
  são contadas por situação e não devem ser somadas entre situações. Valores zero
  das quatro situações são preservados. Status desconhecido ou payload inválido
  gera indisponibilidade, sem adivinhação.
- Evolução usa `data_conclusao` de conclusões CONCLUIDO, não data de validação nem
  percentual histórico de conformidade. Eventos e participantes devem pertencer ao
  workspace autorizado. Sem período: do início do mês de 11 meses atrás até hoje.
  Meses sem registros são omitidos, sem inventar zeros; o aviso explica isso.
- Conflitos consideram eventos ativos e pares únicos de turmas da mesma pessoa.
  Interseção é estrita: término de uma exatamente no início de outra não conflita.
  Sem período: hoje até os próximos 30 dias. Só horários cadastrados, sem verificar
  presença ou disponibilidade externa. Não há cancelamento de inscrições.
- Simulação exige cargo exato (ignorando caixa) ou unidade exata e uma NR não
  revogada; se ambos fornecidos, aplica os dois. Usa registros já marcados aplicáveis
  e explicita as lacunas. Não marca registros como aplicáveis, muda exigências ou
  presume qual seria a regra legal. A população de equipe exclui o próprio gestor.
- Origem consulta uma conformidade de pessoa autorizada; conformidades de unidade
  não estão incluídas. Referências de formulário/conclusão não conferem acesso ao
  conteúdo ou aos arquivos. Não há consulta automática a Mongo ou ao armazenamento.
- Filtros incompatíveis exigem esclarecimento em vez de serem silenciosamente
  ignorados. `inicio` e `fim` são datas ISO, devem vir juntos, em ordem e até 366 dias.
  Identidade, role e workspace não são argumentos aceitos pelas tools.

## Limites, blocos e clientes

Contrato exato, exemplos JSON e instruções para web/mobile estão em
[blocos analíticos e paginação local](contrato-dados-analiticos.md).

As dez consultas usam quantidade padrão 100, coleta máxima 200 e lotes internos
máximos de 50, no mesmo snapshot autorizado e com ordenação estável. O frontend
pagina o conjunto recebido sem novas chamadas. `limite` é quantidade solicitada;
`pagina` só aceita 1. Não pedir próxima página ao chatbot neste fluxo.

Retorno informa solicitada, retornada, total, limitado, tem_mais e motivo_limite.
Há limite de 2 MiB no envelope analítico e orçamento conservador de 1.500.000 bytes
para linhas. Total e indicadores consideram a população autorizada, não a amostra.
As demais tools antigas mantêm seus contratos próprios; não houve remoção geral
da paginação de agenda/mensagens/notificações.

Ordem: `indicadores` v1, `matriz_conformidade` v1 ou `tabela` v2, gráfico opcional,
`contexto_dados` v1. Fonte, filtros efetivos, referência, período e critérios seguem
estruturados. Histórico mantém a mesma ordem e versões do POST, sem recalcular
mensagens antigas. Bloqueio do Juiz/guardrail impede emitir esses blocos.

Gráficos são agregados independentemente da quantidade de linhas e limitados a
12 categorias completas; acima disso são omitidos com aviso. Comparação entre
unidades usa contagens no gráfico, e percentual com numerador/denominador na tabela;
base zero produz null. Evolução usa linhas por padrão. Meses sem registro são
omitidos, não zeros. Não somar pessoas únicas entre situações ou confundir pessoa
com pares pessoa/NR. Cobertura global é anterior ao filtro de encaminhamento da
lista; o universo está explícito nos cartões e critérios.

Mais leituras SQL calculam métricas e gráficos globais, com timeout de statement
existente e sem fan-out por pessoa. Evento faz um único GET Java, timeout 12s,
limite 2 MB e sem redirects. Nenhuma chamada extra de IA foi adicionada por linha,
cartão ou gráfico; o Juiz recebe agregados e plano visual sem as linhas brutas.

**Publicação coordenada:** web e mobile precisam suportar os três tipos novos e
paginação local de tabela v2/matriz antes do deploy da API. PR em rascunho enquanto
há essa dependência. Não houve deploy nem migração de dados.

Resultados de leitura real e limitações estão em [homologação](homologacao-consultas-gestao.md).


Fontes de critérios: [documentação do banco](https://astro-inter.github.io/astro-database/), regras existentes de `SCOPE`/`fn_seleciona_unidades` e `EventService` da API Java (consulta de participantes e exclusividade de validação pelo criador). Critérios de simulação são hipóteses cadastrais explícitas desta tool.

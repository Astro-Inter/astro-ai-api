# Homologação de leitura das tools operacionais

Data: 30/10/2026 (America/Sao_Paulo).

## Método e proteção de credenciais

Foram usadas as duas identidades autorizadas pelo usuário, sem gravar senhas.
A autenticação de teste criou sessões temporárias por custom token Firebase,
confirmou o ID token e consultou o papel no PostgreSQL. Os papéis retornados foram
GESTOR_WORKSPACE e GESTOR. **As senhas fornecidas não foram testadas**; esse método
valida as identidades e o acesso das tools, não o login com senha.
Tokens permaneceram em memória e foram removidos do contexto após cada execução.
O relatório contém somente perfis, contagens, status e duração; sem nomes,
e-mails, identificadores de registros, senhas ou tokens.

As chamadas das tools acessaram o PostgreSQL real em conexão somente leitura e a
API Java via GET com o token da respectiva identidade. Não houve publicação,
reindexação, alteração de cadastro, formulário ou evento, nem envio de mensagem.
O login temporário pode atualizar metadados de autenticação do Firebase.

## Resultados

| Tool | Gestor de workspace | Gestor |
|---|---|---|
| Indicadores por cargo | OK, 4 categorias; soma de 143 outros usuários | OK, 4 categorias; soma de 121 outros usuários |
| Vencimentos nos próximos 30 dias | Sem registros | Sem registros |
| Pendências da NR-10 | OK, 4 registros | Sem registros |
| Conformidade de unidade | OK, 9 registros autorizados | OK, 9 registros autorizados |
| Formulários pendentes recebidos | Sem registros | Sem registros |
| Respostas de uma instância concluída | OK | OK |
| Detalhes de evento autorizado | OK | OK |
| Candidatos elegíveis para NR-10 | OK, 9 registros | OK, 9 registros |

Contagens são o retrato observado, não dados fixos do produto. Tabelas retornadas
passaram pela validação Pydantic do contrato público. A soma dos indicadores foi
comparada a uma consulta independente de usuários autorizados. As linhas de
pendências também foram comparadas aos e-mails autorizados, sem persistir essa lista.
A função Java de unidades autorizadas retornou quatro unidades para ambas as
identidades. Isso não elimina a restrição adicional à própria unidade nas tools
SQL de equipe do gestor comum. Endpoints Java mantêm suas regras próprias; não se
deve inferir que todos os recursos do produto têm o mesmo escopo de equipe.
Resultados vazios não validam visualmente linhas de vencimento ou formulários
pendentes com dados positivos; esses casos seguem cobertos por simulações.

## Correções identificadas

- Dois usuários pré-cadastrados não tinham Firebase UID. A exclusão do próprio
  solicitante por comparação de Firebase UID eliminava também esses registros.
  A consulta agora compara os IDs internos dos usuários; identidade e papel do
  solicitante continuam sendo revalidados pelo banco. A contagem passou a conferir.
- A API Java exige UUID para referência de formulário. O gateway agora pede
  esclarecimento local para referência inválida; HTTP 400/422 também pedem
  correção de filtro, sem apresentar detalhes privados do backend.
- Uma emissão de token falhou inicialmente por diferença de relógio; uma nova
  verificação após espera curta confirmou a identidade. Não foi relaxada a
  verificação de assinatura, revogação ou autorização da aplicação.

## IA real, custo e limites

Foram feitas duas chamadas reais ao roteador Luna pelo OpenRouter, com perguntas
sintéticas: distribuição dos funcionários por cargo e formulários pendentes.
As duas selecionaram a tool esperada. O JSON foi validado pelo contrato
OperationalDecision. Não foram enviados dados pessoais reais nessas chamadas.
Uso observado: 4.805 tokens de entrada + 70 tokens de saída = 4.875 tokens.
Custo total reportado pelo provedor: US$ 0,000465275.

A execução completa do chat (Juiz/guardrails, histórico, paginação e renderização
em blocos) tem cobertura automatizada com serviços simulados. Nesta homologação
real as tools foram chamadas diretamente: não foi criada conversa de teste no
Mongo nem executado um turno completo de chat com dados pessoais reais na IA.
ADMIN/COLABORADOR tiveram negações verificadas em testes simulados; não foram
usadas contas reais desses perfis. Web/mobile ainda precisam de verificação visual.
As mudanças continuam locais, sem deploy.

## Controles negativos reais

Nos dois perfis, um evento existente em outro workspace retornou `sem_dados`,
sem linhas. Uma referência UUID de formulário inexistente também retornou
`sem_dados`, sem conteúdo. Referências inválidas foram rejeitadas localmente
com `esclarecer`. No gestor comum, um contexto de teste alegando papel
GESTOR_WORKSPACE não retornou registros: o papel foi revalidado no PostgreSQL.
Isso complementa, sem substituir, os testes automatizados de negação por perfil.

Após as correções encontradas, os 76 testes específicos das tools passaram.


## Rodada de perguntas de gráficos

Quatro perguntas reais adicionais ao roteador Luna foram executadas com a
identidade de gestor de workspace. As consultas do banco foram somente leitura,
sem criar sessões de chat nem enviar resultados pessoais aos modelos:

| Pergunta | Resultado |
|---|---|
| Mostre um gráfico de barras dos funcionários por cargo. | Barras, 3 categorias, 120 colaboradores |
| Mostre um gráfico de pizza da equipe por situação cadastral. | Setores, 3 categorias, 143 outros usuários funcionais |
| Compare os funcionários ativos por cargo em um gráfico de barras. | Barras, 3 categorias, 116 colaboradores ativos |
| Faça um gráfico de linhas da evolução mensal da equipe. | Esclarecimento: cadastro atual não é série histórica |

As contagens são um retrato do ambiente na execução. Nas três consultas positivas,
o agrupamento, filtros e tipo do gráfico foram preservados e os dados validaram
os contratos dos blocos públicos. As diferenças de contagem entre equipe e
funcionários são intencionais: funcionários filtra COLABORADOR; equipe permite
os outros papéis funcionais visíveis ao solicitante. Também houve simulação do
fluxo completo de barras/setores após revisão e testes dos componentes web;
esta rodada não representa validação visual no navegador ou no tablet.

Uso adicional reportado: 10.039 tokens de entrada + 181 de saída = 10.220 tokens,
4 chamadas, US$ 0,000798595. O uso anterior desta documentação é separado.

### Combinações recomendadas

- Indicadores + filtros de RH: já disponível. Cargo por situação ativa ou
  composição por perfil, respeitando filtros e todo o conjunto autorizado.
- Pendências + gráfico de barras por NR ou situação: próximo passo. Agregar as
  pendências completas no SQL antes de paginar; o valor é número de pendências
  (pessoa/NR), não pessoas únicas. Nunca contar só as primeiras linhas.
- Vencimentos + faixas de prazo: próximo passo. Agregar no banco por vencidas,
  0–7, 8–30, 31–60 dias, com limites não sobrepostos e categoria separada para
  ausência de validade. A tool de vencimentos atual exclui datas nulas.
- Histórico de conclusões de treinamentos: linhas já existentes para o próprio
  usuário. Não misturar essa definição com conformidade de toda a empresa.

Nenhuma biblioteca comercial ou assinatura é necessária para esses gráficos.
Mais tipos de gráfico dependem primeiro de agregações e séries corretas; tabelas
acessíveis e fontes devem acompanhar a visualização.


## Agregações de pendências e vencimentos

As duas novas tools foram executadas em leitura com ambos os perfis autorizados.
Pendências retornaram 6 categorias de NR para gestor de workspace e 5 para gestor.
Vencimentos retornaram 5 faixas para ambos, incluindo zeros em “Hoje a 7 dias”
e “8 a 30 dias”, registros vencidos, acima de 30 dias e com validade ausente.
No workspace, acima de 30 dias havia 90 registros de 84 pessoas distintas;
no gestor, 76 registros de 73 pessoas distintas. Esses valores são retratos do
ambiente, não fixtures ou parâmetros do produto. Os contratos de tabela/gráfico
foram validados com os resultados reais, sem expor pessoas nos relatórios.

Duas perguntas sintéticas reais ao Luna selecionaram as ações esperadas:
pendências por NR e conformidades por faixa de prazo. Uso reportado: 5.377 tokens
de entrada + 76 de saída = 5.453 tokens, custo US$ 0,00050769. Sem dados pessoais
reais enviados aos modelos. Sem alterações de negócio ou deploy.

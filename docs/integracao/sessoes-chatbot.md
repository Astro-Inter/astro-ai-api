# Sessões do chatbot para web e mobile — SCRUM-453

As aplicações web e mobile usam os mesmos endpoints e contratos. As sessões
pertencem ao UID Firebase do usuário e são compartilhadas entre as plataformas.
Uma conversa criada em uma delas pode ser listada, aberta e retomada na outra
usando a mesma conta. Status, mensagens e atualizações são comuns aos dois clientes.

Este documento reúne a entrega da API e o roteiro de integração no mobile.
Os mesmos contratos também atendem ao frontend web.

## O que foi implementado na API

Referência da tarefa: **SCRUM-453 — Criar endpoint de sessões na IA**.
Branch de implementação: `feat/SCRUM-453-criar-endpoint-de-sessoes-na-ia`.

- Novo `GET /sessions` para descobrir os UUIDs das conversas do usuário autenticado.
- Paginação por cursor com limite padrão de 20 e máximo de 100 sessões por página.
- Ordenação por atualização mais recente, com UUID decrescente para desempate.
- Inclusão de sessões ativas, em encerramento e encerradas.
- Título derivado da primeira pergunta e prévia da última mensagem em texto simples.
- Datas de criação e atualização em ISO 8601 UTC.
- Gravação do título, prévia e atualização junto com os novos turnos persistidos.
- Compatibilidade com sessões antigas que ainda não têm título ou prévia gravados.
- Retomada por `/iniciar`, preservando o UUID e todas as mensagens.
- Validação da propriedade da sessão nos endpoints de histórico, início, encerramento
  e envio de mensagens, usando a autenticação Firebase existente.
- Contratos, exemplos e erros documentados no OpenAPI e em `/docs`.

O endpoint de histórico já existia e permanece com o mesmo formato de resposta.
O primeiro `POST /chat/messages` sem `session_id` continua criando uma sessão.
A alteração de comportamento em `/iniciar` permite reabrir uma sessão encerrada.

| Operação | Endpoint | Entrega nesta tarefa |
| --- | --- | --- |
| Listar conversas | `GET /sessions` | Novo endpoint. |
| Carregar histórico | `GET /sessions/{session_id}/messages` | Contrato existente preservado. |
| Iniciar ou retomar | `POST /sessions/{session_id}/iniciar` | Retomada de sessões encerradas com o mesmo UUID. |
| Enviar mensagem ou criar conversa | `POST /chat/messages` | Contrato existente; novos turnos também atualizam os metadados da listagem. |
| Encerrar conversa | `POST /sessions/{session_id}/encerrar` | Contrato existente; conversa permanece consultável e pode ser retomada. |

## Autenticação e ambiente

Todas as chamadas usam `Authorization: Bearer <Firebase ID token>` do usuário
já autenticado no Astro. A API valida o token Firebase e o acesso existente ao
Astro. Os clientes web e mobile usam essa autenticação existente. Não envie UID,
identificador de usuário ou workspace: a API determina o dono a partir do token.

Use a URL base configurada para a API de IA no ambiente de integração. Os caminhos
dos exemplos são relativos a essa URL. Confira se o ambiente já está executando a
versão que contém a tarefa SCRUM-453 antes de conectar o fluxo do mobile.

O token enviado deve ser o **Firebase ID token**, obtido pelo fluxo de autenticação
já usado no aplicativo. A API também confere se esse usuário possui acesso ao Astro.
Os clientes usam as credenciais da própria conta; o backend define o escopo da consulta.

| Requisição | Headers |
| --- | --- |
| Todas as operações | `Authorization: Bearer <Firebase ID token>` |
| Envio de mensagem com JSON | Também `Content-Type: application/json` |

`/iniciar` e `/encerrar` não recebem corpo JSON. Os dados das conversas nas respostas
de sucesso têm `Cache-Control: no-store`. Caso o aplicativo mantenha estado local,
associe esse estado à conta autenticada e limpe-o no logout ou na troca de conta.

## Listar conversas

```http
GET /sessions?limit=20
Authorization: Bearer <Firebase ID token>
```

```json
{
  "sessions": [
    {
      "session_id": "94229143-d20b-4766-80a4-05341435c236",
      "title": "NRs para minha unidade",
      "last_message_preview": "Vamos conferir as atividades da unidade…",
      "created_at": "2026-10-08T12:00:00Z",
      "updated_at": "2026-10-08T12:05:00Z",
      "status": "ativa"
    }
  ],
  "next_cursor": null
}
```

| Parâmetro | Contrato |
| --- | --- |
| `limit` | Inteiro de 1 a 100; padrão 20. |
| `cursor` | Opcional na primeira página. Nas próximas, copie `next_cursor` sem modificá-lo. Até 1.024 caracteres. Vinculado ao usuário autenticado. |

O endpoint aceita somente `limit` e `cursor`. Parâmetros adicionais, como `uid`,
`id_user`, `page` ou `offset`, retornam `422`. Na primeira página, omita `cursor`;
enviar `cursor=` vazio também é inválido.

| Campo da resposta | Tipo | Uso no mobile |
| --- | --- | --- |
| `sessions` | Lista | Itens da tela de conversas; pode estar vazia. |
| `session_id` | String UUID | Chave do item e identificador para abrir e continuar a conversa. |
| `title` | String | Título em texto simples, até 80 caracteres. |
| `last_message_preview` | String | Prévia em texto simples, até 200 caracteres; pode estar vazia. |
| `created_at` | String ISO 8601 UTC | Data de criação da conversa. |
| `updated_at` | String ISO 8601 UTC | Data da última atualização da sessão. |
| `status` | `ativa`, `encerrando` ou `encerrada` | Estado atual conhecido da conversa. |
| `next_cursor` | String ou `null` | Posição da próxima página; `null` indica o fim. |

Se `next_cursor` não for `null`, carregue a próxima página com
`GET /sessions?limit=20&cursor=<next_cursor>`, usando o mesmo token/usuário.
O cliente deve codificar o cursor como parâmetro de URL. Pare ao receber `null`.
Sem conversas, a resposta é `{"sessions": [], "next_cursor": null}`.

As sessões são ordenadas por `updated_at` decrescente e, quando a data empata,
UUID decrescente. Os estados `ativa`, `encerrando` e `encerrada` são incluídos.
Cada página contém apenas sessões do usuário autenticado, sem alterar status,
datas ou mensagens. A paginação não é um snapshot: uma sessão atualizada pode
mudar de posição entre páginas. Recarregue a primeira página após enviar uma
mensagem, retomar ou encerrar. Ao trocar de conta, descarte a lista e o cursor.

O título vem da primeira pergunta persistida, com até 80 caracteres. Uma sessão
vazia tem título `Nova conversa` e prévia vazia. A prévia usa a última mensagem
persistida, com até 200 caracteres. Ambos são texto simples, com espaços
normalizados; o corte usa `…`. Cada turno completo atualiza os metadados junto
com as mensagens. Turnos bloqueados que não são persistidos não mudam a prévia.
Sessões antigas sem esses metadados usam o histórico como fonte durante a leitura.
Todas as datas são ISO 8601 UTC, terminadas em `Z`.

No mobile, renderize título e prévia diretamente como texto. Preserve a ordem
recebida da API. Para exibir datas, converta o instante UTC para o fuso usado pela
interface; mantenha o valor original na camada de dados.

`updated_at` também muda em operações que alteram a sessão, como retomada e
encerramento. Ele representa a última atualização da sessão, e pode ser posterior
ao horário da última mensagem.

## Abrir uma conversa

Use o UUID retornado na lista:

```http
GET /sessions/94229143-d20b-4766-80a4-05341435c236/messages
Authorization: Bearer <Firebase ID token>
```

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "status": "encerrada",
  "total": 2,
  "mensagens": [
    {"role": "user", "content": "NRs para minha unidade"},
    {"role": "assistant", "content": "Vamos conferir as atividades da unidade."}
  ]
}
```

As mensagens vêm na ordem em que foram persistidas. O contrato existente usa
`mensagens`, com papéis `user` e `assistant`; o conteúdo mantém o formato salvo.
Consultar o histórico de uma sessão encerrada não a reabre. Use o status dessa
resposta para decidir o próximo passo, pois ele pode ter mudado desde a listagem.

| Campo | Contrato |
| --- | --- |
| `session_id` | Mesmo UUID solicitado na URL. |
| `status` | Estado da sessão no momento da consulta. |
| `total` | Número de mensagens, incluindo perguntas e respostas; não é o número de turnos. |
| `mensagens` | Lista ordenada de objetos com `role` e `content`. |
| `role` | `user` ou `assistant`. |
| `content` | Texto original persistido da mensagem. |

O endpoint retorna o histórico completo dentro dos limites da sessão; não possui
paginação de mensagens nesta entrega. Uma sessão sem mensagens retorna
`total: 0` e `mensagens: []`. As mensagens não incluem ID individual ou data
individual no contrato atual; use uma chave local para a renderização, se necessário.

Observe os nomes de transporte: a listagem usa `sessions`, o histórico usa
`mensagens` e a resposta do chat usa `resposta`. Preserve esses nomes nos modelos
de leitura do JSON, mesmo que o aplicativo use outros nomes internamente.

## Retomar e enviar mensagens

Se a sessão estiver `ativa`, envie diretamente `POST /chat/messages` com seu
`session_id`. Se estiver `encerrada`, retome antes de enviar:

```http
POST /sessions/94229143-d20b-4766-80a4-05341435c236/iniciar
Authorization: Bearer <Firebase ID token>
```

A chamada não recebe corpo JSON e retorna:

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "status": "ativa",
  "resumo": null,
  "resumo_indexado": false
}
```

O UUID, o histórico e a data de criação são preservados. Repetir `/iniciar` em
uma sessão já ativa não apaga mensagens nem atualiza as datas. O resumo anterior
e qualquer confirmação de ação pendente são invalidados na retomada. O próximo
encerramento refaz o resumo incluindo a continuação e atualiza o mesmo ponto
vetorial. Os limites totais de 200 mensagens e 240 mil caracteres permanecem;
ao atingi-los, crie outra conversa.

Se o estado for `encerrando`, repita `/encerrar` para concluir a operação antes
de retomar. `/iniciar` retorna `409` durante encerramento ou outra operação da
mesma conversa. Espere a operação em andamento terminar antes de tentar novamente.

```http
POST /chat/messages
Authorization: Bearer <Firebase ID token>
Content-Type: application/json

{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "message": "E quais treinamentos preciso concluir?"
}
```

A resposta mantém `session_id`, `resposta` e `agentes_chamados`. Enviar para uma
sessão encerrada antes de `/iniciar` retorna `409`; enviar para uma sessão de
outro usuário retorna `404`. O parâmetro existente `markdown=false` continua
disponível em `/chat/messages` para respostas em texto simples.

Exemplo ilustrativo de resposta do envio (`200`):

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "resposta": "Vou conferir os treinamentos atribuídos a você.",
  "agentes_chamados": ["guardrail_entrada", "roteador", "agenda", "orquestrador", "juiz", "guardrail_saida"]
}
```

O conteúdo da resposta e os agentes variam conforme a pergunta. `resposta` contém
a mensagem pública do assistente; `agentes_chamados` é uma lista de nomes e pode
estar vazia. O envio retorna JSON, sem streaming neste contrato.

`message` deve ter de 1 a 4.000 caracteres e não pode ser composto só por espaços.
`session_id`, quando enviado, deve ser um UUID válido. O corpo aceita somente
esses dois campos; enviar outros campos retorna `422`.

O formato padrão da resposta é Markdown. Para uma interface que exiba texto simples,
envie `POST /chat/messages?markdown=false`, tanto na primeira mensagem quanto nas
continuações. Essa opção vale para a resposta desse envio. O histórico devolve o
conteúdo que foi salvo; mensagens antigas podem conter Markdown e precisam ser
renderizadas pelo tratamento de conteúdo já usado no aplicativo.

Respostas de guardrails podem ser devolvidas com `200` sem persistir o turno.
Por isso, o histórico consultado por `GET /sessions/{session_id}/messages` é a fonte
para reconstruir a conversa ao reabri-la. O cliente não deve concluir que uma
mensagem foi persistida apenas pelo código HTTP do envio.

## Criar uma conversa

Na primeira mensagem, omita `session_id`:

```http
POST /chat/messages
Authorization: Bearer <Firebase ID token>
Content-Type: application/json

{"message": "NRs para minha unidade"}
```

Guarde o UUID retornado em `session_id` e use-o nos próximos envios. Recarregue a
listagem para exibir o título e a prévia. Não é necessário outro endpoint de
criação. O contrato anterior de `/iniciar` para um UUID ainda inexistente permanece
disponível; tanto web quanto mobile podem criar a conversa pela primeira mensagem.

O botão de nova conversa pode abrir uma tela local vazia, com `session_id` ainda
ausente. O UUID passa a ser definido quando a primeira mensagem retorna com sucesso.
Durante esse envio, bloqueie o botão para evitar duas requisições de criação.

## Encerrar e tratar erros

`POST /sessions/{session_id}/encerrar`, sem corpo JSON, mantém o histórico e
retorna `status: "encerrada"`, `resumo` e `resumo_indexado`. Uma sessão vazia
encerra com resumo nulo. Repetir um encerramento já concluído retorna o mesmo
resultado. A conversa continua acessível na listagem e pode ser retomada.

Exemplo ilustrativo de resposta (`200`):

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "status": "encerrada",
  "resumo": "O usuário consultou as NRs e os treinamentos de sua unidade.",
  "resumo_indexado": true
}
```

`resumo_indexado` indica o resultado da indexação do resumo, e não deve ser usado
para decidir se o histórico pode ser aberto. Uma sessão encerrada continua
consultável mesmo quando esse campo é `false`.

| HTTP | Causa e ação do cliente |
| --- | --- |
| `400` | Cursor malformado, incompatível ou de outra conta. Reinicie a listagem sem cursor. |
| `401` | Token ausente, inválido, expirado ou revogado. Renove a autenticação Firebase. |
| `403` | Usuário autenticado sem acesso reconhecido ao Astro. |
| `404` | Sessão inexistente ou de outro usuário ao consultar/encerrar; UUID alheio também é rejeitado no chat e em `/iniciar`. |
| `409` | Operação concorrente, encerramento pendente, sessão encerrada no chat ou limite total de histórico. Confira o status e aplique o fluxo correspondente. |
| `422` | UUID, corpo, `limit`, comprimento do cursor ou parâmetro de query inválido. Corrija a requisição. |
| `429` | Limite de operações simultâneas atingido; tente novamente depois. |
| `502` | Resposta dos agentes ou embedding inválido durante chat/encerramento. |
| `503` | Autenticação, autorização, banco ou provedor temporariamente indisponível. |
| `504` | Tempo de operação excedido. No encerramento, repita `/encerrar` para concluir. |

Erros da aplicação usam `{"detail": "mensagem do erro"}`. Por exemplo, um
cursor inválido retorna `{"detail": "Cursor invalido para esta consulta."}`.
Erros de validação `422` usam o formato FastAPI com uma lista em `detail`,
indicando o campo e a causa. Os dados das conversas têm `Cache-Control: no-store`.

Exemplo de autenticação inválida (`401`):

```json
{"detail": "Credenciais de autenticacao invalidas."}
```

Exemplo de sessão indisponível para esse usuário (`404`):

```json
{"detail": "Conversa nao encontrada."}
```

Exemplo de validação do limite (`422`, formato ilustrativo):

```json
{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": ["query", "limit"],
      "msg": "Input should be less than or equal to 100",
      "input": "101",
      "ctx": {"le": 100}
    }
  ]
}
```

O texto de `detail` ajuda a apresentar a causa, mas o cliente deve usar o código
HTTP e consultar o estado da sessão para decidir o fluxo. Em especial, `409`
tem mais de uma causa; não significa sempre que a conversa foi encerrada.

## Roteiro de implementação no mobile

### Estado local e modelos

Mantenha na camada de estado da funcionalidade:

- Lista de sessões e `next_cursor` da página atual.
- UID da conta à qual o estado local pertence, sem enviá-lo nos requests.
- `session_id` selecionado, ou ausência de UUID para uma conversa nova.
- `status` da sessão carregada e lista de `mensagens` do histórico.
- Estados de carregamento inicial, próxima página, abertura, envio, retomada
  e encerramento, além dos respectivos erros.

O Firebase ID token deve ser obtido pela integração de autenticação existente.
Ao trocar de conta, cancele ou descarte respostas pendentes da conta anterior e
limpe lista, cursor, sessão selecionada e histórico local.

### Tela de conversas

1. Ao abrir a tela, faça `GET /sessions?limit=20` com o token da conta atual.
2. Exiba título, prévia e data de cada sessão, usando `session_id` como chave.
3. Se `sessions` estiver vazia, mostre o estado vazio com ação para nova conversa.
4. Ao carregar mais, envie o cursor recebido e acrescente os itens à lista.
5. Una os itens por `session_id` para evitar duplicação visual entre requisições.
6. Impeça duas requisições simultâneas da mesma página.
7. Ao atualizar a lista, substitua-a pela primeira página e descarte o cursor antigo.
8. Pare de carregar mais quando `next_cursor` for `null`.

Não derive o próximo cursor de datas ou do último UUID: use sempre o valor
devolvido pela API. Ao receber `400`, reinicie a listagem sem cursor.

### Tela da conversa e botão de continuar

1. Ao selecionar uma sessão, carregue `GET /sessions/{session_id}/messages`.
2. Renderize as mensagens na ordem recebida e guarde o status da resposta.
3. A abertura para leitura funciona com qualquer um dos três estados.
4. Se estiver `ativa`, permita o envio com o UUID selecionado.
5. Se estiver `encerrada`, chame `/iniciar` ao escolher continuar a conversa
   ou antes do primeiro novo envio; aguarde sucesso para enviar a mensagem.
6. Se estiver `encerrando`, permita consultar o histórico e concluir o encerramento
   por `/encerrar`. Depois de concluído, a sessão pode ser retomada por `/iniciar`.
7. Ao receber `404`, informe que a conversa não está disponível e atualize a lista.
   Não tente criar uma sessão vazia com esse UUID como recuperação do histórico.

Abrir a tela não precisa chamar `/iniciar`. A retomada é uma operação de escrita
e deve acontecer quando o usuário decidir continuar a conversa. Use os UUIDs
obtidos da API para esse fluxo.

### Envio, atualização e encerramento

1. Valide o texto e bloqueie envios simultâneos para a mesma conversa.
2. Para conversa nova, envie somente `message`. Para continuação, envie também
   o `session_id` selecionado.
3. Após sucesso, guarde o UUID da resposta e exiba `resposta` como mensagem do assistente.
4. Atualize a primeira página da lista para refletir título, prévia e posição recentes.
5. Quando o usuário escolher encerrar, faça `/encerrar` e aguarde o resultado.
6. Após encerramento concluído, guarde `status: "encerrada"` e atualize a lista.
7. Ao voltar ao aplicativo ou à conversa, recarregue o histórico/status para
   incorporar mudanças feitas no web ou em outro dispositivo.

Sair da tela não exige encerrar a sessão. Vincule o encerramento à ação definida
pelo produto. Esta entrega usa consultas HTTP; a sincronização entre clientes
ocorre ao recarregar lista e histórico.

Pseudocódigo do fluxo de envio:

```text
enviar(texto):
    validar texto entre 1 e 4.000 caracteres
    se há envio, retomada ou encerramento local em andamento:
        aguardar essa operação

    se existe sessão selecionada e seu status é encerrando:
        solicitar conclusão do encerramento antes de continuar
        retornar

    se existe sessão selecionada e seu status é encerrada:
        chamar POST /sessions/{UUID}/iniciar
        continuar somente após sucesso
        marcar status local como ativa

    corpo = { message: texto }
    se existe UUID selecionado:
        adicionar session_id ao corpo

    chamar POST /chat/messages com corpo
    após sucesso:
        guardar session_id retornado
        apresentar resposta do assistente
        atualizar primeira página da lista

    em erro:
        preservar texto digitado
        tratar código HTTP e atualizar status/histórico quando necessário
```

### Concorrência e falhas de rede

Web e mobile podem operar sobre o mesmo UUID. Mesmo com o botão local bloqueado,
outra plataforma pode estar enviando ou encerrando a conversa; o backend pode
retornar `409`. Aguarde a operação e consulte novamente o histórico/status antes
de decidir entre enviar, retomar ou finalizar o encerramento.

Em falha de rede ou timeout no envio, a resposta pode não ter chegado ao cliente
mesmo que o servidor tenha concluído a gravação. Para um UUID conhecido, consulte
o histórico antes de reenviar. O contrato não possui chave de idempotência para
mensagens; um reenvio pode gerar outro turno.

Se a falha ocorrer na primeira mensagem e o cliente ainda não recebeu o UUID,
recarregue a listagem para o usuário conferir a conversa criada. Não faça retry
automático de criação: cada envio sem `session_id` pode criar outra sessão.
O título sozinho não é um identificador único para correlacionar requisições.

No encerramento, a API permite repetir `/encerrar` para concluir etapas pendentes.
Em `401`, atualize a autenticação Firebase antes de tentar novamente. Evite
repetições ilimitadas de requisições que retornem erro.

## Conferência da integração mobile

- [ ] Listagem com token válido carrega somente as sessões da conta atual.
- [ ] Conta sem conversas apresenta estado vazio.
- [ ] Mais de 20 sessões podem ser carregadas por cursor, sem duplicação visual.
- [ ] A lista mantém a ordem da API, inclusive quando duas datas empatam.
- [ ] Nova conversa envia a primeira mensagem sem UUID e guarda o UUID retornado.
- [ ] Continuação envia o mesmo UUID; navegar para outra tela e voltar preserva o histórico.
- [ ] Histórico de sessão encerrada pode ser aberto sem mudar o status.
- [ ] Retomada chama `/iniciar` e continua com o mesmo UUID e mensagens anteriores.
- [ ] Sessão em encerramento recebe tratamento antes de novo envio.
- [ ] Título e prévia aparecem como texto simples; histórico respeita o conteúdo salvo.
- [ ] Datas UTC são convertidas apenas para apresentação no fuso da interface.
- [ ] Encerramento mantém a conversa na listagem e permite consulta posterior.
- [ ] Mudanças feitas no web aparecem no mobile ao atualizar lista e histórico.
- [ ] Token expirado, erros de validação, concorrência e falhas de rede têm tratamento.
- [ ] Logout/troca de conta limpa os dados locais e descarta respostas antigas.

## Validação realizada no backend

Foram adicionados 36 casos de teste para a entrega, cobrindo autenticação,
isolamento entre usuários, lista vazia, ordenação e desempate, paginação,
validação de cursor/limite, metadados, retomada sem perda de mensagens,
concorrência e documentação OpenAPI.

Os 68 testes de sessões e memória passaram. A suíte completa teve 646 testes
validados; oito testes de ingestão precisaram ser reexecutados com um diretório
temporário acessível devido a permissões do ambiente. Firebase e Mongo foram
simulados nos testes da tarefa. A validação de contrato OpenAPI também passou
após o ajuste da documentação para web e mobile.

Para executar os testes específicos no repositório da API:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sessions.py tests/test_memory.py -q
```

O OpenAPI em `/openapi.json` e a interface `/docs` incluem autenticação Bearer,
parâmetros, modelos, exemplos e respostas de erro. Renomear e excluir conversas
não fazem parte desta tarefa.

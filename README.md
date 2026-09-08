# astro-ai-api

## Login de desenvolvimento

Instale as dependências com `python -m pip install -e .` e inicie com
`python -m uvicorn app.main:app --reload`.

No `.env`, configure `APP_ENV=development`, `ENABLE_DEV_LOGIN=true` e
`FIREBASE_WEB_API_KEY` com a chave do mesmo projeto das credenciais Firebase Admin.
A chave pode ser encontrada em `client[].api_key[].current_key` do
`google-services.json` do aplicativo correspondente ou na configuração web do Firebase.
Não é necessário carregar o JSON mobile inteiro no servidor. Se a chave tiver
restrições exclusivas de Android/iOS, utilize uma chave apropriada para chamadas
do backend, permitindo a API Firebase Authentication (Identity Toolkit).
Habilite Email/Password no Firebase Authentication e utilize um usuário de teste.

`POST /auth/login` recebe JSON:

```json
{"email": "usuario@example.com", "password": "senha-do-usuario-de-teste"}
```

A resposta contém `access_token` (Firebase ID Token), `expires_in` (segundos) e
`token_type` (`Bearer`). Envie o token nas rotas protegidas como
`Authorization: Bearer <access_token>`. O login pode ser executado em `/docs`;
use o botão Authorize para informar o token e acessar o chat. `GET /health`
continua público.

O backend usa a API REST oficial do Firebase e não cria JWT próprio nem retorna
refresh tokens. A validação Bearer mantém a verificação de revogação e de usuários
desabilitados. O antigo header `X-Dev-Auth-Token` não concede mais acesso.

Com `ENABLE_DEV_LOGIN=false` ou fora de development/test, o login não é registrado
e retorna 404, inclusive não aparecendo no OpenAPI. Reinicie a API após editar `.env`.
Use HTTPS fora do localhost e não registre corpos de login nem headers de autorização.

## Chat e grafos (SCRUM-186)

`POST /chat/messages` exige `Authorization: Bearer <access_token>` com um Firebase
ID Token válido. O UID é extraído do token no backend, nunca recebido no corpo.
No `/docs`, autorize com o token retornado pelo login e envie:

```json
{
  "message": "Olá, como você pode me ajudar?"
}
```

Exemplo de resposta (o texto e o caminho dependem da mensagem):

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "resposta": "Olá! Posso ajudar com RH, SST, Agenda e dúvidas sobre normas.",
  "agentes_chamados": ["guardrail_entrada", "roteador", "guardrail_saida"]
}
```

Para continuar, envie o `session_id` retornado junto da próxima `message`.
Se o ID informado ainda não existir, o chat cria a sessão no MongoDB. Se existir,
retoma o documento, desde que pertença ao UID autenticado e esteja ativo.
Sem `session_id`, uma nova conversa é criada com UUID gerado no backend.
O backend define o horário de
referência em `America/Sao_Paulo`; não existe campo `timezone` na requisição.
Não envie UID, role, workspace ou histórico no JSON.

### Fluxo implementado

- Guardrail de entrada: aprova, bloqueia ou pede esclarecimento.
- Roteador: escolhe RH, SST, Agenda ou FAQ; saudações e esclarecimentos podem
  receber resposta direta, revisada pelo guardrail de saída.
- RH, SST e Agenda: cada um possui um subgrafo compilado; o resultado estruturado
  segue para o Orquestrador e depois para o guardrail de saída.
- FAQ: subgrafo com nós de consulta de normas e resposta direta, sem Orquestrador,
  conforme a modelagem. **A busca documental ainda não está integrada**: o nó de
  consulta retorna ausência de fontes e a resposta é um aviso fixo de
  indisponibilidade, sem chamar o modelo ou inventar normas. O prompt FAQ será
  conectado quando houver um retriever autorizado.

Os demais agentes usam os prompts existentes, incluindo o prompt inicial comum.
Nesta etapa só a memória de conversas acessa os bancos; não existem ferramentas
de registros de negócio, calendário ou escrita operacional. Os
especialistas podem orientar e esclarecer, mas não consultar registros, criar
eventos ou executar solicitações. Autenticação não concede acesso automático a
dados de outras pessoas ou empresas; a autorização dessas integrações será
implementada com as respectivas ferramentas.

### Modelos e configuração

Atualize as dependências com `python -m pip install -e .` e reinicie a API.
Os nomes de modelos ficam em `app/infrastructure/llm/models.py`, não no `.env`:

- `GROQ_API_KEY`: necessária para guardrails, Roteador e Orquestrador, usando
  `openai/gpt-oss-20b`.
- `MISTRAL_API_KEY`: quando preenchida, os especialistas usam
  `mistral-small-latest`. Sem ela, usam `openai/gpt-oss-120b` no Groq. Isso é uma
  escolha por configuração, não um fallback automático em caso de erro da Mistral.

Cada mensagem pode gerar de uma a seis chamadas de modelo, além de um embedding
quando houver busca semântica, com custos e latência
dos provedores. `LANGSMITH_*` é lido pelo SDK quando o tracing está habilitado;
traces podem conter mensagens, contexto do usuário e respostas. Habilite somente
quando esse envio de dados estiver autorizado. Bearer e chaves não são incluídos
nos prompts. Nenhuma variável de ambiente nova é necessária para o chat.

## Histórico persistente e sessões (SCRUM-187)

Configure `MONGODB_URI`, `MONGODB_DATABASE`, `QDRANT_URL`, `QDRANT_API_KEY`,
`GROQ_API_KEY` e `MISTRAL_API_KEY` no `.env`. Não há variáveis novas.
A chave Mistral é necessária para embeddings, mesmo que os agentes de chat usem
Groq. Reinicie a API após alterar configurações.

O MongoDB usa a coleção `sessoes`; o Qdrant usa `memoria_resumos`. Os nomes ficam
no código. O Mongo cria a coleção e o índice de histórico no primeiro acesso;
o usuário do banco precisa das permissões correspondentes. A API não apaga nem
recria a coleção Qdrant: ela deve existir com um único vetor denso **1024/Cosine**
e índice keyword `id_user` (tenant). Um índice ausente é criado no primeiro uso.
O modelo `mistral-embed` gera vetores de 1024 dimensões; não misture embeddings de
modelos diferentes na mesma coleção. [Documentação Mistral](https://docs.mistral.ai/resources/cookbooks/mistral-embeddings-embeddings).

### Documento MongoDB

Um documento por sessão, seguindo a modelagem fornecida. Exemplo ilustrativo:

```javascript
{
  _id: "94229143-d20b-4766-80a4-05341435c236", // session_id (UUID como string)
  id_user: "uid-do-firebase",                // string; nunca um ID recebido no corpo
  iniciada_em: ISODate("2026-09-07T12:00:00Z"),
  atualizada_em: ISODate("2026-09-07T12:01:00Z"),
  resumo: "",
  mensagens: [
    { role: "human", content: "Olá!" },
    { role: "assistant", content: "Como posso ajudar?" }
  ],
  status: "ativa",
  resumo_indexado: false
}
```

Campos auxiliares: `ultima_rota`, `encerrada_em`, `resumo_parcial` e `resumo_ate`
para progresso do resumo; `lock_token` e `lock_ate` enquanto uma operação reserva
a sessão. Datas são BSON datetime em UTC. Um documento do formato básico, sem
`status`, é tratado como ativo; `_id` deve ser UUID em string e `id_user` deve ser
o UID Firebase. Não existe fallback para um usuário de teste.

### Iniciar e encerrar

As duas rotas usam o mesmo Bearer do chat, sem corpo JSON:

- `POST /sessions/{session_id}/iniciar`: cria uma sessão vazia ou retorna a sessão
  ativa existente do próprio usuário. O cliente fornece um UUID. Esta chamada é
  opcional: `POST /chat/messages` também cria a sessão quando necessário.
- `POST /sessions/{session_id}/encerrar`: congela a conversa, gera o resumo com
  Groq, salva-o no Mongo, cria o embedding com Mistral e faz upsert no Qdrant.
  O ID do ponto é o mesmo `session_id`; o payload inclui `id_user`, `session_id`,
  `resumo`, `iniciada_em` e `modelo_embedding`.

Exemplo de resposta de encerramento:

```json
{
  "session_id": "94229143-d20b-4766-80a4-05341435c236",
  "status": "encerrada",
  "resumo": "O usuário perguntou sobre RH. A consulta de registros estava indisponível.",
  "resumo_indexado": true
}
```

Uma sessão vazia encerra sem chamar LLM/Qdrant, com `resumo: null` e
`resumo_indexado: false`. Repetir o encerramento concluído retorna o mesmo
resultado, sem novas chamadas de IA. Uma sessão encerrada não pode ser reaberta
ou receber mensagens: use outro UUID.

Se houver falha, o estado fica `encerrando`: **repita a mesma rota de encerramento**.
O resumo já gerado e o progresso por trechos são preservados, e o upsert com ID
estável evita duplicação. Só retornamos `encerrada` depois das gravações confirmadas.
Não há transação distribuída entre Mongo e Qdrant nem worker automático de retry;
uma falha após a gravação no Qdrant ainda pode exigir nova tentativa para finalizar
o estado no Mongo. Sessões em encerramento não entram na busca de memória.

### Memória do roteador

O histórico recente da sessão atual vem do Mongo em cada mensagem e sobrevive a
reinícios. O roteador pode solicitar `buscar_historico` por um nó do grafo,
usando o protocolo interno `MEMORY={"busca":"assunto"}`. Isso não é um endpoint
nem deve ser enviado pelo frontend. O backend injeta o UID e limita a consulta
a uma por mensagem, sempre após aprovação do guardrail de entrada.

A busca semântica filtra `id_user` no Qdrant e retorna até três IDs. O Mongo
revalida a propriedade e o estado encerrado antes de entregar resumos e trechos
finais das conversas. Assim, nem um payload vetorial com ID alheio concede acesso.
Para uma pergunta genérica sobre conversas passadas, a busca vazia retorna os
três resumos mais recentes diretamente do Mongo. Se o Qdrant/Mistral falhar,
retorna os recentes do Mongo com indicação de busca semântica indisponível.
Sem resultados semânticos, não afirmamos que conversas diferentes deram match.

Resumos e mensagens são dados, não instruções ou prova de execução de ações.
Respostas do roteador sobre memória passam pelo guardrail de saída. Não confunda
histórico com normas oficiais, saldos atuais, agenda real ou autorização de acesso.

### Limites e erros

Somente turnos públicos completos são salvos, com `human` e `assistant` gravados
no mesmo update. Turnos bloqueados e resultados intermediários não são salvos.
O prompt recebe até 10 turnos/24 mil caracteres da sessão atual; o restante continua
no Mongo. A sessão aceita até 200 mensagens (100 turnos) e reserva espaço dentro
de 240 mil caracteres. Ao atingir o limite, encerre-a e inicie outra.
Resumos longos são processados por trechos com progresso persistido.

Não há expiração automática ou exclusão do histórico. Há reserva atômica por
sessão no Mongo, com lease de 180 segundos e token de posse, para serializar chat
e encerramento mesmo em workers diferentes. Requisições têm timeout de 120
segundos; após queda abrupta de um worker, aguarde o lease expirar. Os relógios
dos servidores devem estar sincronizados. Limite de 20 operações simultâneas por
processo; configure rate limiting e política de retenção antes de produção.

- `401`: Bearer ausente ou inválido.
- `404`: sessão de outro usuário, ou sessão inexistente ao encerrar.
- `409`: operação simultânea, sessão encerrada/em encerramento ou limite atingido.
- `422`: corpo ou UUID inválido; `timezone` continua fora do contrato.
- `502`: resposta/embedding inválido; `503`: banco ou provedor indisponível.
- `504`: timeout; no encerramento, repita a chamada para continuar.

O MongoDB é obrigatório para conversar; não existe fallback para histórico em RAM.
O health permanece público e não testa dependências externas.

### Testes

Instale `python -m pip install -e ".[dev]"` e execute `python -m pytest -q`.
Testes usam repositório/LLMs simulados e Qdrant local em memória, sem escrever nas
coleções reais ou enviar traces. Cobrem propriedade por UID, retomada, concorrência,
resumo por trechos, repetição de encerramento e falhas parciais entre os bancos.

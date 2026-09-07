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
Sem `session_id`, uma nova conversa é criada. O backend define o horário de
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
Nesta etapa não existem ferramentas de banco, calendário ou escrita: os
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

Cada mensagem pode gerar de uma a cinco chamadas de modelo, com custos e latência
dos provedores. `LANGSMITH_*` é lido pelo SDK quando o tracing está habilitado;
traces podem conter mensagens, contexto do usuário e respostas. Habilite somente
quando esse envio de dados estiver autorizado. Bearer e chaves não são incluídos
nos prompts. Nenhuma variável de ambiente nova é necessária para o chat.

### Memória e limites

Histórico em memória, isolado por UID e sessão, apenas com os turnos públicos
completos; resultados intermediários e turnos bloqueados não são armazenados.
Cada sessão guarda até 10 turnos e 24 mil caracteres, expira após uma hora sem uso
e desaparece ao reiniciar a API. Há limite de mil sessões e 20 requisições ativas
por processo. Use **um worker** nesta etapa; persistência compartilhada via banco
ou Redis não está implementada. Esses limites não substituem rate limiting por
usuário em produção.

A mensagem aceita até 4 mil caracteres e a execução tem timeout total de 120
segundos. Respostas inválidas dos agentes resultam em 502; falhas de provedor em
503; timeout em 504. Sessão inexistente, expirada ou de outro UID retorna 404;
uma segunda mensagem simultânea na mesma sessão retorna 409. Ao receber 404 por
expiração, comece uma nova conversa omitindo `session_id`.

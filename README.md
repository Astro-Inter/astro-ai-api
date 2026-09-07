# astro-ai-api

## Login de desenvolvimento

Instale as dependências com `python -m pip install -e ".[dev]"` e inicie com
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
o botão Authorize estará disponível quando houver rotas que usem a dependência
`CurrentUserDependency`. `GET /health` continua público.

O backend usa a API REST oficial do Firebase e não cria JWT próprio nem retorna
refresh tokens. A validação Bearer mantém a verificação de revogação e de usuários
desabilitados. O antigo header `X-Dev-Auth-Token` não concede mais acesso.

Com `ENABLE_DEV_LOGIN=false` ou fora de development/test, o login não é registrado
e retorna 404, inclusive não aparecendo no OpenAPI. Reinicie a API após editar `.env`.
Use HTTPS fora do localhost e não registre corpos de login nem headers de autorização.

Execute os testes com `python -m pytest -q`. As chamadas Firebase são simuladas;
os testes não usam contas ou credenciais reais.

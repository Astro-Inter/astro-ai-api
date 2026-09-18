# Deploy do Astro AI API

A mesma imagem executa a API principal e, com outro comando, o agente de
pesquisa A2A. Credenciais são fornecidas apenas em tempo de execução.

## Imagem Docker

Na raiz do repositório:

```bash
docker build -t astro-ai-api:local .
docker run --rm -p 8000:8000 --env-file .env astro-ai-api:local
```

O processo padrão escuta `0.0.0.0` na variável `PORT` (padrão `8000`). Verifique
`http://localhost:8000/health`. Para executar o agente A2A usando a mesma imagem:

```bash
docker run --rm -p 8090:8090 --env-file .env \
  -e PORT=8090 -e A2A_BIND_HOST=0.0.0.0 \
  astro-ai-api:local python -m app.a2a.public_research_server
```

Nesse caso, `A2A_PUBLIC_RESEARCH_URL` deve corresponder à URL pela qual o agente
é acessado e `A2A_SHARED_TOKEN` deve ter ao menos 32 caracteres.

## Ambiente provisório no Render

1. Envie o repositório para GitHub e, no Render, escolha **New > Blueprint**.
2. Selecione o repositório. O Render detectará o `render.yaml` e criará
   `astro-ai-api` e `astro-ai-a2a` na mesma região.
3. Preencha as variáveis marcadas como secretas durante a criação. Use os valores
   do `.env`; não envie o arquivo nem o JSON do Firebase ao Git.
4. Aguarde os dois health checks e teste `https://astro-ai-api.onrender.com/health`
   e `https://astro-ai-api.onrender.com/docs` usando o endereço real atribuído.

Para confirmar qual código está atendendo às perguntas, consulte `GET /version`
na API. O campo `commit` deve coincidir com o SHA do deploy no Render; `unknown`
indica que o ambiente não forneceu `RENDER_GIT_COMMIT`.

O Blueprint gera a chave A2A e a compartilha automaticamente com a API. A URL
externa do agente também é ligada automaticamente. O endpoint A2A exige essa
chave; somente `/health` é público.

O ambiente provisório habilita `/auth/login` para facilitar os testes pelo
Swagger. Use apenas contas de teste. Antes de transformar esse serviço em
produção, altere `APP_ENV` para `production` e `ENABLE_DEV_LOGIN` para `false`.

Depois que o Render fornecer a URL da API, configure
`GOOGLE_OAUTH_REDIRECT_URI` como
`https://SEU-SERVICO.onrender.com/integracoes/google-calendar/callback` e cadastre
exatamente a mesma URI no Google Cloud. PostgreSQL, MongoDB, Redis e Qdrant
também precisam aceitar conexões originadas pelo Render.

Planos gratuitos podem hibernar. Na primeira consulta, o agente A2A pode estar
iniciando; a API mantém o fallback local para o MCP Fetch.

## Kubernetes e AWS EKS

Os manifests usam Kustomize. Cada Pod tem dois containers: a API na porta `8000`
e o agente A2A como sidecar na `8090`. Somente a API é exposta pelo Service; o
A2A é acessado por `localhost` e continua protegido pela chave compartilhada.

Crie o Secret sem versionar seus valores:

```bash
kubectl apply -f deploy/k8s/base/namespace.yaml
cp deploy/k8s/secrets.env.example deploy/k8s/secrets.env
kubectl -n astro-ai create secret generic astro-ai-api-secrets \
  --from-env-file=deploy/k8s/secrets.env
```

Preencha `deploy/k8s/secrets.env` antes do último comando e gere
`A2A_SHARED_TOKEN` com `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
O arquivo local está ignorado pelo Git.

Para visualizar o resultado sem aplicar:

```bash
kubectl kustomize deploy/k8s/base
kubectl kustomize deploy/k8s/overlays/aws
```

Antes do EKS, substitua no overlay AWS:

- `ACCOUNT_ID`, `REGION` e a tag da imagem no ECR;
- o ARN real do certificado ACM;
- `astro-api.example.com` pelo domínio real;
- `GOOGLE_OAUTH_REDIRECT_URI` pela callback HTTPS desse domínio.

O Ingress pressupõe o AWS Load Balancer Controller e usa ALB com targets do tipo
`ip`. No CI/CD, publique uma tag imutável no ECR (por exemplo, o SHA do commit),
atualize `newTag` e aplique o overlay. Em produção, forneça os segredos pelo AWS
Secrets Manager/External Secrets ou pelo mecanismo adotado pela plataforma, em
vez de criá-los manualmente.

O Deployment inicia com duas réplicas, rolling update sem indisponibilidade,
probes de inicialização/prontidão/vida, recursos definidos, usuário sem root,
filesystem somente leitura e um volume temporário limitado.

# Cache seletivo Redis da IA

## Escopo e impacto

Esta alteração reduz repetições de duas leituras: fontes públicas do MCP entre processos/instâncias e URLs de fotos já emitidas pela API Java. Não adiciona chamadas de IA. Não cacheia respostas finais, classificação, Juiz, guardrails, permissões, histórico, PDFs, listas de usuários ou agregações privadas. Esses resultados dependem do usuário e de dados atuais; um cache genérico poderia causar vazamento ou respostas desatualizadas.

Contratos POST/histórico, ordem dos blocos e validações não mudam. Web e mobile não precisam de ajuste. O ganho nas fontes públicas ocorre quando URL e parâmetros se repetem dentro do TTL; o LLM e a revisão continuam sendo executados. Não há promessa de redução de latência em consultas que não usam esses caminhos.

## Fontes públicas oficiais

- Chave: `ia:cache:fonte_publica:v1:{hash_consulta}` (String JSON).
- Hash: SHA-256 de `json.dumps([url, max_length, start_index, raw], ensure_ascii=True, separators=(',', ':'))`, codificado em UTF-8. Não inclui prompts, UID ou credenciais.
- Valor: `{"versao":1,"criado_em":<epoch segundos>,"expira_em":<epoch segundos>,"resultado":<contrato MCP>}`. O resultado preserva `status`, `url`, `conteudo`, `truncado`, `proximo_indice` e `protocolo`.
- TTL: `SET ... EX FETCH_MCP_CACHE_TTL_SECONDS`, padrão 900 segundos, máximo 86400. `0` desativa os dois níveis. Hits usam somente `GET`: não renovam o prazo. Relógios das instâncias devem estar sincronizados.
- Tamanho máximo do valor compartilhado: 262144 bytes. Resultado vazio, erro, envelope inválido, expirado ou versão/URL incompatível não são reutilizados. Não há cache negativo nem retorno de conteúdo vencido em falhas.
- A allowlist HTTPS é validada antes de todo hit, inclusive local. URLs com query string ou fragmento não vão para o Redis. Somente fontes públicas, sem credenciais, podem usar esse cache; não o reutilize para fontes privadas.
- Cache local: até 128 entradas por cliente MCP, cópias independentes; um hit Redis mantém apenas o prazo restante. Reduzir o TTL também restringe entradas existentes.
- Redis: orçamento de 300 ms por operação, timeouts de socket/conexão de 100 ms e pausa de novas tentativas por 30 segundos por instância após falha. A leitura original via MCP continua em caso de indisponibilidade. Um miss normal faz GET e, se a coleta for bem-sucedida e elegível, SET. Um hit compartilhado evita o subprocesso e a coleta HTTP; hits locais evitam o próprio Redis.
- Não há lock distribuído: misses simultâneos podem repetir a coleta. A revisão do conteúdo por Juiz/guardrails continua igual; conteúdo externo é dado não confiável.

Configuração no backend (sem novos segredos):

```dotenv
REDIS_URL=<segredo de conexão do ambiente>
FETCH_MCP_REDIS_CACHE_ENABLED=true
FETCH_MCP_CACHE_TTL_SECONDS=900
FETCH_MCP_ALLOWED_DOMAINS=gov.br
```

`FETCH_MCP_REDIS_CACHE_ENABLED=false` mantém apenas o cache local. Render tem processos API e A2A separados: configure `REDIS_URL` também no A2A para compartilhar as coletas feitas por ele. O exemplo Render e o ConfigMap Kubernetes incluem o toggle. Nada foi alterado em produção.

## Fotos autorizadas e compatibilidade Java

A API Java atual usa `usuario:foto:{id_usuario}`. A IA passa a reutilizar essa mesma chave String contendo uma URL temporária, em vez de `user:avatar:{id}`. Nenhuma chave antiga é removida ou migrada; expira naturalmente. A definição existente no catálogo do banco foi ampliada para documentar o consumidor/produtor IA.

A IA primeiro consulta autorização e caminho atual no PostgreSQL, depois faz um único `MGET` para até 200 fotos autorizadas. Um hit só é usado se host, caminho do objeto, formato da assinatura e validade restante superior a 30 segundos forem compatíveis. Hits não geram nem renovam assinatura. Ausência/falha do cache segue o fluxo de assinatura já existente; ausência de foto/configuração/autorização preserva o fallback sem foto.

A API Java mantém seu `PXAT` baseado na validade da assinatura. Em um miss, a IA preserva assinatura de 24 horas e `SET EX 86370`, com margem de 30 segundos. Isso se aplica a fotos; o contrato e a expiração dos PDFs não mudam. URLs e credenciais nunca devem entrar em logs, prompts ou documentação.

Configure `AVATAR_REDIS_URL` (fallback `REDIS_URL`) no mesmo banco Redis usado pela Java, além de `AVATAR_R2_*` e `R2_BUCKET_AVATARS` compatíveis. Redis e armazenamento divergentes podem impedir o reuso; não devem ampliar permissões. Não é necessário copiar credenciais para clientes.

## Operação e publicação

Invalidação normal por TTL. Em incidente de conteúdo público incorreto, desative o cache e, se necessário, remova somente a chave exata conhecida ou incremente a versão do prefixo com uma alteração de código. Não execute `KEYS`, `FLUSHDB` ou limpeza de outros serviços. Fotos continuam seguindo invalidação do produtor Java e verificação de caminho/expiração da IA.

Os logs de fontes públicas registram apenas estados `hit`/`miss` em DEBUG e `invalid`/`unavailable` em INFO, sem URLs, valores ou texto de exceções de conexão. Logs de avatar não contêm assinaturas. Os testes automatizados usam Redis, MCP e signer simulados; não conectam aos ambientes reais. Cobrem allowlist antes do cache, expiração sem renovação, TTL reduzido, falhas, limite de tempo, contratos, limite de memória e MGET autorizado.

A publicação depende da revisão das PRs da IA e do catálogo Redis. A implementação está em branch separada da homologação PR #35, sem mesclar alterações pendentes nem efetuar merge/deploy. A família de chave está documentada no repositório `Astro-Inter/astro-database` e é descartável, sem migração de dados.

## Evidências desta alteração

- Suíte completa: 1056 testes passaram, com 15 avisos de dependências; após acrescentar uma regressão adicional de falha do MGET, a suíte focada foi repetida.
- Catálogo do banco: 162 testes passaram; build estático concluído.
- Confirmação real limitada: duas coletas públicas oficiais, com clientes MCP distintos, retornaram `ok` em aproximadamente 5,36 s e 2,99 s. O Redis configurado ficou indisponível dentro do orçamento por operação e ambos seguiram pelo MCP. Portanto essa execução confirma o fallback, não um ganho real de cache compartilhado. Hits, TTL e ausência de renovação foram confirmados com Redis simulado; homologação com Redis acessível ainda é necessária.
- Nenhuma chamada de LLM, embedding ou reindexação foi realizada nesta alteração. Nenhum dado privado foi consultado nessa confirmação; nenhum deploy ou mudança de configuração remota foi realizado.

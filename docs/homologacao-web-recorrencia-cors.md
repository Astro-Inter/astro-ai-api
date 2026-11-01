# Recorrência web e CORS de PDFs sobre a7fbe68

## Revisão e amostra

Em 01/11/2026, produção continuava em
`a7fbe680520c90355f17f766fb6d21efce4e8f38`. A PR #35, com as correções de cobertura,
contexto e expiração BSON, estava aberta e ainda não publicada. A investigação
acrescenta correções e regressões nessa mesma branch; sem merge, deploy ou mudança
do bucket. Contas e cadastros autorizados foram reutilizados, sem criar dados de
negócio. A tentativa de PDF solicitada gerou um arquivo e histórico de homologação.

Foram feitas duas sequências limitadas de cobertura → listagem de 100 → barras
por cargo com referência à listagem. Todas incluíram Origin do web publicado.

| Fluxo | Rodada 1 | Rodada 2 |
| --- | --- | --- |
| Cobertura | 200, indicadores/tabela/barras/contexto | 200, só texto de validação |
| Listagem | 200, tabela | 200, tabela |
| Barras com base na listagem | 200, gráfico e tabela | 200, gráfico e tabela |
| Primeira tentativa de PDF | 200, arquivo v1 | Não repetida para evitar outro upload |

Essa amostra confirma a intermitência de cobertura na revisão antiga; não estima
taxa de erro do produto. A recusa de barras e o 503 da primeira geração relatados
não foram reproduzidos nesta rodada. Não se declara que deixaram de existir.

Correlações para os logs do runtime:

- Cobertura completa: fbc1009b-cf3b-48f0-80eb-8bed8944f038.
- Cobertura somente texto: a292d449-9900-481d-a5c3-0fc532618719.
- Barras: f5084d57-e8c8-45cc-bdf9-1ab72aa5459f e ca617563-b707-48d3-ab20-b5146b2f6531.
- Primeira tentativa de PDF bem-sucedida: 0c1cd03d-845a-4a84-a641-61bc9996f650.

Não há acesso aos logs dos workers publicados neste contexto. Portanto esses
request IDs não autorizam inferir status intermediário, quota, timeout ou falha de
upload. Tokens, URLs assinadas, sessões e dados pessoais não foram versionados.

## Revisão, resumo e erros 503

A investigação anterior reproduziu com Juiz real a omissão dos valores no resumo
de cobertura. A correção pendente na PR #35 traz contagens, etapas exclusivas e
percentual n/d, sem alterar valores ou forçar aprovação. Os vereditos continuam
podendo bloquear a entrega; não há repetição automática até obter aprovação.

Nas barras, o resumo antigo transcrevia grupos e terminava com “Total: 4 registros”,
referindo-se às categorias e não às pessoas. Embora isso não prove a causa de uma
recusa específica, é uma ambiguidade concreta do texto de revisão. Quando o retorno
agregado está completo e possui gráfico validável, o resumo agora distingue o
total de pessoas das categorias e explicita a abrangência completa pelos filtros.
Não soma páginas, não usa a amostra da listagem e não promete total global para
uma página parcial de categorias. Outros gráficos, como pendências por NR, não
somam pessoas únicas entre NRs.

A validação local real posterior enviou 100 de 141 pessoas e entregou quatro
categorias somando as mesmas 141, com POST e histórico iguais. Não houve recusa
nessa execução; a aprovação segue necessária. Regressões simuladas confirmam que
rejeição mantém somente o aviso explícito e não libera gráfico/PDF.

O 503 original do PDF ainda precisa de horário, request ID e logs do worker.
Falhas capturadas de upload/renderização retornam mensagem sem cartão de sucesso;
não são automaticamente equivalentes a um 503 de provedor, revisão ou histórico.
Não se alteraram timeouts, limites ou número de tentativas. CORS de download também
não explica sozinho um HTTP 503 retornado antes de a API entregar um arquivo.

Diagnóstico acrescentado para futuras ocorrências:

- Erros OpenRouter preservam a etapa e o motivo técnico controlado.
- `chat_request_rejected` registra HTTP, etapa e motivo permitidos, com o request ID
  da requisição. Retry-After é preservado. Não registra corpo do provedor, tokens,
  mensagens livres ou URL do arquivo.
- `chat_review_not_approved` distingue revisar/rejeitado, tool e quantidade de
  problemas. Não registra as justificativas/problemas em texto livre, pois podem
  conter dados pessoais. HTTP 200 com recusa permanece distinto de falha 503.

## Evidência de CORS do armazenamento

Uma URL **já existente e válida** foi usada; nenhuma assinatura foi renovada.
Os testes HTTP do armazenamento usaram cliente separado, sem token Astro.

| Origin | GET parcial | Access-Control-Allow-Origin | Preflight GET/Range |
| --- | --- | --- | --- |
| https://astro-inter.github.io | 206 | ausente | 403, sem allow-origin |
| https://unauthorized.example | 206 | ausente | 403, sem allow-origin |

O GET válido distingue isso de expiração da assinatura: o objeto foi acessível,
mas o navegador não pode ler o resultado por fetch nessa origem. A API de chat
retornou seu próprio allow-origin; isso não configura CORS do R2. CORS também não
é autorização: quem possui uma assinatura válida ainda pode usá-la fora de um
navegador. Expiração e autorização permanecem indispensáveis.

`GetBucketCors` respondeu **AccessDenied** à credencial disponível. Isso impede
confirmar se falta toda a política, uma origem, método ou header. Não se conclui
“bucket sem política” apenas pelo header ausente. Não foi feita tentativa de
sobrescrever uma política desconhecida nem de ampliar permissões da credencial.

Segundo a [documentação oficial de CORS do R2](https://developers.cloudflare.com/r2/buckets/cors/),
URLs assinadas continuam precisando de CORS para fetch no navegador; testes devem
enviar Origin. Uma URL expirada pode retornar 403 sem headers CORS mesmo depois da
configuração correta. Por isso não se usa renovação para contornar expiração nem
se assume que todo 403 é CORS. Uma assinatura de GET não é autorização de HEAD.

## Política candidata e aplicação por administrador

Arquivo revisável em [pdf-download-cors.s3.json](../infra/r2/pdf-download-cors.s3.json):

- Origem HTTPS conhecida: https://astro-inter.github.io, sem path nem curinga.
- Método GET, correspondente à assinatura atual de download. Sem PUT/DELETE ou
  Authorization. Não alterar acesso público do bucket.
- Header Range; exposição de Content-Disposition, Content-Length, Content-Range e
  ETag para nome, tamanho e download parcial no frontend.
- Cache de preflight de 3600s, independente do prazo de cinco horas da assinatura.
- Outras origens só devem ser incluídas após confirmação de que são autorizadas.

**Não aplicar esse arquivo como substituição cega do bucket.** Um administrador
deve exportar e guardar a política atual para rollback, conferir se o bucket é
compartilhado e preservar regras de outros serviços. A API de configuração de CORS
substitui a política; o exemplo contém somente a regra candidata do chatbot.

O planejador offline preserva regras alheias e substitui somente seu ID gerenciado:

```powershell
python -m app.scripts.plan_pdf_cors --current politica-atual.json --origin https://astro-inter.github.io
```

Ele não conecta ao R2, não aplica mudanças, não gera URLs e exige uma política
atual explicitamente fornecida. Não tratar AccessDenied como política vazia. O
JSON usa o formato S3 CORSRules; no painel Cloudflare, usar o array de regras do
formato exigido pelo painel, conferindo a documentação, não colar um wrapper errado.

Após revisar e autorizar a aplicação com um administrador:

1. Aplicar a regra preservando as demais; manter backup da configuração anterior.
2. Esperar propagação e testar um link ainda válido, sem assinar de novo.
3. Origin autorizado: GET/Range e preflight devem permitir a origem exata e expor
   os headers necessários. Origin não autorizado não deve ganhar acesso por CORS.
4. Testar fetch no navegador sem credentials/include ou token Astro para o R2.
5. Testar arquivo expirado sem renovar assinatura; UI usa estado/prazo do arquivo.
6. Se necessário, restaurar exatamente a política anterior; não apagar objetos.

A aplicação remota permanece pendente por falta de acesso à política atual e
necessidade de revisão administrativa. Não houve mudança de bucket ou deploy.

## Verificação

Testes simulados cobrem o resumo completo versus categorias, veredito negativo
sem liberação de componentes, 503 correlacionado, etapa/motivo sanitizados,
Retry-After, política restrita e preservação de regras de outros serviços. Não há
chamada de IA extra adicionada pelo diagnóstico ou pela configuração CORS.
Validação final: **1.065 testes passaram**, com 15 avisos de dependências. Compileall, diff --check e comparação com segredos locais passaram. A sequência local real acrescentou quatro inferências, 14.878 tokens e US$ 0,001921325 medidos pelo OpenRouter. As sete requisições de chat na produção não retornam usage/custo ao cliente e não integram essa soma. Não houve embeddings. Relatórios brutos e scripts de reprodução ficam locais e não incluem assinaturas em mensagens/logs.

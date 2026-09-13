# astro-ai-api

## Ingestão de PDFs do FAQ

O script `app/scripts/ingest_faq.py` substitui **todos os pontos** de `faq_chunks`
pelos PDFs informados, em cada execução. Não altera `memoria_resumos` e não apaga
a coleção em si: mantém configurações e índices. Não execute duas ingestões
simultâneas nem grave nessa coleção por outro processo durante a substituição.

Instale as dependências e execute na raiz do projeto:

```powershell
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m app.scripts.ingest_faq "C:\documentos\faq.pdf"
```

Para vários arquivos, informe todos na mesma execução:

```powershell
.venv\Scripts\python.exe -m app.scripts.ingest_faq "C:\documentos\rh.pdf" "C:\documentos\sst.pdf"
```

Ou informe uma pasta para ler todos os PDFs diretamente nela (sem subpastas):

```powershell
.venv\Scripts\python.exe -m app.scripts.ingest_faq "C:\documentos\faq"
```

Os caminhos acima são exemplos. Use os caminhos reais dos seus PDFs. O script usa
`QDRANT_URL`, `QDRANT_API_KEY` e `MISTRAL_API_KEY` já existentes no `.env`.
`faq_chunks` deve existir com um único vetor denso de 1024 dimensões e Cosine;
vetor padrão ou nomeado são aceitos. Não há configuração de coleção/modelo no `.env`.

Fluxo: extrair texto por página → dividir em chunks de 700 caracteres com
sobreposição de 150 → gerar embeddings `mistral-embed` em lotes de até 50 →
apagar todos os pontos antigos → inserir os novos em lotes → conferir a contagem.
Cada ponto contém `page_content`, `page_number` (começando em zero), `source`
(nome do PDF, sem caminho local completo) e `modelo_embedding`.

**Sempre envie a base completa.** Se executar primeiro com A.pdf e depois apenas
com B.pdf, a coleção final conterá somente B.pdf. Entradas inválidas, PDFs sem
texto e falhas ao gerar embeddings abortam antes da limpeza. PDFs digitalizados
precisam de OCR prévio; páginas sem texto são avisadas e ignoradas. Revise a
extração de tabelas e layouts complexos antes de usar os documentos como fonte.

Os novos pontos são preparados em memória antes de apagar os antigos. Não há
rollback automático nem substituição atômica: uma falha durante a gravação pode
deixar a coleção vazia/parcial. Execute novamente com todos os PDFs; durante a
limpeza a consulta de FAQ pode ficar temporariamente indisponível. Mantenha os
PDFs originais ou snapshots próprios se precisar recuperar a versão anterior.
Gerar embeddings envia os textos à Mistral e pode gerar custos.

O script apenas carrega a base; nenhum upload é disparado ao iniciar a API. Nas
respostas, o número interno da página é convertido para começar em 1.

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

Depois de validar o Firebase ID Token, cada rota protegida consulta o PostgreSQL
com `SELECT fn_retornar_nivel_acesso(%s)`, usando o UID como parâmetro. O claim
`role` do Firebase é ignorado. Os perfis aceitos são `ADMIN`, `GESTOR`,
`GESTOR_WORKSPACE` e `FUNCIONARIO`; `SEM_ACESSO` retorna `403`. Falha, retorno
inválido ou role desconhecida retorna `503`, sem expor detalhes da conexão.
Configure `DATABASE_URL` e conceda ao usuário do banco somente as permissões
necessárias para conectar e executar essa função.

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
  "agentes_chamados": ["guardrail_entrada", "roteador", "juiz", "guardrail_saida"]
}
```

Para continuar, envie o `session_id` retornado junto da próxima `message`.
Se o ID informado ainda não existir, o chat cria a sessão no MongoDB. Se existir,
retoma o documento, desde que pertença ao UID autenticado e esteja ativo.
Sem `session_id`, uma nova conversa é criada com UUID gerado no backend.
O backend define o horário de
referência em `America/Sao_Paulo`; não existe campo `timezone` na requisição.
Não envie UID, role, workspace ou histórico no JSON.
O backend instancia `CurrentUser` com `uid` e `role` após a consulta ao PostgreSQL.
Esse objeto fica em `usuario_atual` no estado compartilhado do LangGraph; todos
os nós podem utilizá-lo em tools futuras, e os agentes recebem os dois campos no
contexto confiável da aplicação.

### Fluxo implementado

- Guardrail de entrada: aprova, bloqueia ou pede esclarecimento.
- Roteador: escolhe RH, SST, Agenda, Eventos ou FAQ; saudações e esclarecimentos podem
  receber resposta direta, revisada pelo guardrail de saída.
- RH, SST, Agenda e Eventos: cada um possui um subgrafo compilado; o resultado estruturado
  segue para o Orquestrador, o Juiz e o guardrail de saída.
- FAQ: subgrafo com nós de consulta de normas e resposta direta, sem Orquestrador,
  conforme a modelagem. A pergunta é transformada em embedding `mistral-embed` e
  consulta até cinco trechos de `faq_chunks` por similaridade Cosine. Somente
  resultados com score mínimo de 0,35 são enviados ao agente, como dados não
  confiáveis e nunca como instruções. A resposta usa apenas esses trechos e cita
  nome do PDF e página. Sem resultados relevantes, informa que a informação não
  foi encontrada; falhas de Qdrant ou embeddings retornam `503` sem inventar uma
  resposta. A consulta é somente leitura e não altera os pontos ingeridos.
- Juiz: toda resposta candidata, inclusive respostas diretas e do FAQ, passa por
  uma avaliação estruturada antes do guardrail de saída. O Juiz verifica relevância,
  coerência, sustentação nas evidências, fontes, execução de operações, privacidade
  e segurança. Ele retorna apenas `aprovado`, `revisar` ou `rejeitado`, com motivo
  e problemas; não responde ao usuário nem reescreve a candidata. O guardrail
  corrige ou bloqueia avaliações negativas, e a aplicação rejeita uma aprovação
  que tente ignorar o Juiz.

Os demais agentes usam os prompts existentes, incluindo o prompt inicial comum.
No fluxo automático atual, a memória acessa o histórico, a tool de RH consulta
os usuários permitidos pelo perfil autenticado, a tool de SST consulta NRs na
collection autorizada e a tool de Eventos lê treinamentos atribuídos ao usuário.
O Roteador pode enviar mensagens após uma prévia e uma
confirmação explícita; ainda não há tools de negócio para Agenda. Os especialistas
podem orientar e esclarecer, mas não criar eventos ou executar outras solicitações.
Autenticação não
concede acesso automático a dados de outras pessoas ou empresas; cada ferramenta
deve aplicar sua própria regra de autorização.

### Modelos e configuração

Atualize as dependências com `python -m pip install -e .` e reinicie a API.
Os nomes de modelos ficam em `app/infrastructure/llm/models.py`, não no `.env`:

- `GROQ_API_KEY`: necessária para guardrails, Roteador, Juiz e Orquestrador, usando
  `openai/gpt-oss-20b`.
- `MISTRAL_API_KEY`: quando preenchida, os especialistas usam
  `mistral-small-latest` como primeira opção. Sem ela, ou quando a chamada à
  Mistral falhar, os especialistas usam `openai/gpt-oss-20b` no Groq. A falha
  somente é devolvida pela API se os dois provedores falharem.

Cada mensagem pode gerar de uma a sete chamadas de modelo, além de um embedding
quando houver busca semântica de histórico ou FAQ, com custos e latência
dos provedores. `LANGSMITH_*` é lido pelo SDK quando o tracing está habilitado;
traces podem conter mensagens, contexto do usuário e respostas. Habilite somente
quando esse envio de dados estiver autorizado. Bearer e chaves não são incluídos
nos prompts. Nenhuma variável de ambiente nova é necessária para o chat.

### Tools de consulta de usuários do RH

`app/modules/rh/tools.py` contém duas tools LangChain somente leitura. A tool
`buscar_meus_dados` localiza exclusivamente o usuário autenticado pelo Firebase UID
do `CurrentUser` e retorna nome, e-mail, CPF, perfil, cargo, unidade, modalidade,
status e data de cadastro. Ela não expõe filtros ou identificadores ao modelo.

A tool `buscar_outros_usuarios` pesquisa exclusivamente outras pessoas no PostgreSQL.
Ela recebe `CurrentUser` pelo contexto
confiável do backend e filtros validados de status, tipo, nome, cargo e limite
de resultados. Nome e cargo usam busca literal por trecho; curingas `%`, `_` e `\`
são escapados antes do `ILIKE`. Status, tipos e limite são parâmetros da consulta,
nunca SQL produzido pelo modelo.

`ADMIN` pode pesquisar todos os usuários. `GESTOR_WORKSPACE` pesquisa os perfis
`GESTOR`, `GESTOR_WORKSPACE` e `FUNCIONARIO` somente no workspace associado à sua
unidade. `GESTOR` pesquisa apenas `GESTOR` e `FUNCIONARIO` da própria unidade.
`FUNCIONARIO` não pode executar a consulta de terceiros e acessa somente seus dados
pela tool `buscar_meus_dados`.
A consulta de terceiros exclui o próprio Firebase UID, retorna no máximo 50 registros
e apenas nome, e-mail, tipo, cargo,
unidade, modalidade e status. A conexão é somente leitura e possui timeout.

A tool está registrada exclusivamente no subgrafo do agente de RH. Antes de responder
sobre dados cadastrais ou profissionais, o agente decide os filtros, a tool aplica o
escopo do usuário autenticado e o backend envia o resultado confirmado diretamente
ao fluxo de validação. Isso evita uma segunda geração de JSON entre a consulta e a resposta.

Para reduzir latência e tokens, consultas concluídas por `buscar_outros_usuarios` não
passam por uma segunda chamada do especialista nem pelo Orquestrador. O backend
formata os dados confirmados, o Juiz os valida e, quando aprovados, o guardrail de
saída preserva deterministicamente a resposta. Casos reprovados continuam usando
o revisor de saída. O Roteador recebe no máximo seis mensagens anteriores e cada
especialista, dez. Após uma falha da Mistral, os especialistas usam Groq durante
cinco minutos antes de tentar a Mistral novamente.

### Tool de consulta de NRs do SST

`app/modules/sst/tools.py` registra a tool LangChain `consultar_nrs`, somente
leitura, para consultar a collection `nrs` do MongoDB. Ela aceita uma ou várias
NRs pelos respectivos números, pesquisa textual nos campos `nome`, `objetivo`,
`descricao`, `aplicabilidade` e `usabilidade`, além de filtros opcionais de
revogação e público de uso. Também é possível pedir somente campos específicos.

O nome da collection e a estrutura da consulta ficam no backend: o modelo não
recebe uma query MongoDB livre. Termos de pesquisa são escapados antes do regex,
listas e limites são validados. O modo de listagem retorna somente número, nome,
situação e última atualização, com até 50 documentos por página. O detalhamento
completo é reservado a uma NR específica; comparações retornam somente os campos
solicitados e no máximo dez documentos. Os resultados são ordenados pelo número
da NR e datas são serializadas em ISO 8601.

Perguntas sobre NRs são encaminhadas pelo Roteador ao subgrafo de SST. O agente
decide os filtros, a tool consulta o MongoDB e o backend formata a resposta com a
referência à collection e ao documento utilizado. A resposta e a evidência da tool
seguem para o Juiz e o guardrail de saída. Textos extensos não são duplicados na
evidência do Juiz; ele recebe a resposta determinística e metadados compactos da
consulta. A mesma configuração `MONGODB_URI` e
`MONGODB_DATABASE` usada pelo histórico é reutilizada; nenhuma variável nova é
necessária.

A tool `consultar_nrs_obrigatorias` usa o Firebase UID injetado pelo backend e
consulta no PostgreSQL somente as NRs vigentes aplicáveis ao próprio usuário. A
regra combina o cargo e a unidade atuais: a NR precisa estar vinculada ao cargo em
`cargo_nr` e à unidade em `unidade_nr`. O resultado contém cargo, unidade, número,
título e intervalo de reciclagem. A tool não aceita UID, nome ou cargo informados
pelo modelo, e administradores sem vínculo funcional recebem resultado não aplicável.
A origem técnica permanece somente na evidência interna enviada ao Juiz e não é
exibida na resposta ao usuário.

Os joins diretos já usam chaves primárias e relacionamentos pequenos, portanto uma
view comum não produziria ganho de desempenho por si só. Se essa regra passar a ser
reutilizada por outras APIs ou relatórios, uma view como
`vw_nrs_obrigatorias_usuario` pode centralizar a interseção entre cargo e unidade;
índices continuam sendo o recurso responsável pelo desempenho da consulta.

### Tool de envio de mensagens do Roteador

`app/modules/roteador/tools.py` registra a tool `enviar_mensagem`. O usuário
informa nome ou e-mail do destinatário e o texto desejado. O backend resolve o
Firebase UID autenticado para o `id_usuario` do PostgreSQL e restringe a pesquisa
a pessoas ativas do mesmo workspace. IDs internos nunca são aceitos como entrada
do modelo. Pesquisa por e-mail é exata; pesquisa por nome é literal por trecho e
exige o e-mail quando houver mais de uma correspondência.

O primeiro pedido nunca grava a mensagem, mesmo que o modelo tente confirmar o
envio. Pedidos simples como “mande um oi para a Rosa” já chegam à prévia na
primeira resposta; “oi” é o texto e o remetente vem da autenticação, sem precisar
repetir quem é. Se houver complementos na mesma conversa, o roteador usa o
histórico recente para aproveitar nome e texto já informados. A aplicação mostra
uma prévia e armazena na sessão um rascunho com ID estável. Somente uma
confirmação explícita em uma mensagem seguinte, na mesma sessão e sem alterações
no destinatário ou no texto, autoriza a gravação. Repetir uma confirmação após
resposta incerta não duplica o documento. “Sim”, “pode mandar” e “é isso mesmo
que eu quero enviar” são exemplos de confirmação após a prévia.

As mensagens confirmadas ficam na collection fixa `mensagens`, com `_id`,
`id_envia`, `id_recebe`, `mensagem` e `data` em UTC. Os campos
`id_envia` e `id_recebe` são IDs do PostgreSQL, não Firebase UIDs. A mesma
configuração `MONGODB_URI` e `MONGODB_DATABASE` já usada pelo histórico é
reutilizada; nenhuma variável de ambiente nova é necessária.

### Tool de consulta de conversas do Roteador

`consultar_conversas` lê as mensagens trocadas com uma pessoa identificada por
nome ou e-mail, sem aceitar IDs informados pelo modelo. A identidade do usuário
vem da autenticação; o PostgreSQL resolve a outra pessoa somente dentro do mesmo
workspace. Se houver mais de uma correspondência por nome, a consulta pede o
e-mail. Administradores, que não possuem workspace funcional de mensagens, não
usam essa tool.

A busca na collection `mensagens` inclui os dois sentidos da conversa, mas apenas
registros em que o usuário autenticado é um dos participantes. Os resultados
vêm do mais recente ao mais antigo, com cinco mensagens por página por padrão
(máximo de dez). Textos longos são apresentados como trechos de até 500
caracteres para limitar o contexto da IA. Por exemplo: “Mostre minhas últimas
mensagens com Rosa Maduda” ou “Mostre a página 2 das minhas mensagens com Rosa
Maduda”. Pedidos explícitos com nome ou e-mail já seguem direto à consulta, sem
depender da classificação do modelo.

### Tool de consulta de notificações do Roteador

`consultar_notificacoes` lê a collection `notificacoes` usando o `id_usuario`
resolvido no PostgreSQL a partir do Firebase UID autenticado. A ferramenta não
aceita identificadores de usuário na entrada e nunca consulta notificações de
outras pessoas. Os resultados são ordenados por `data_criacao`, do mais recente
ao mais antigo, e paginados (cinco por página, no máximo dez). Textos longos são
apresentados como trechos de até 500 caracteres. Exemplo: “Mostre minhas
notificações” ou “Mostre a página 2 das minhas notificações”. O esquema atual
contém apenas `id_usuario`, `mensagem` e `data_criacao`; portanto, a tool não
classifica notificações como lidas, pendentes ou vencidas. Para uma collection
grande, recomenda-se um índice composto em `id_usuario` e `data_criacao`.

### Tool de consulta de treinamentos do agente de Eventos

`app/modules/eventos/tools.py` registra `consultar_treinamentos`, disponível
somente no subgrafo de Eventos. A ferramenta resolve o Firebase UID autenticado
para `usuario.id_usuario` no PostgreSQL e lê suas inscrições em
`turma_funcionario`, juntando `turma`, `evento`, `conclusao_evento` e o título da
NR quando houver. Não aceita UID ou ID de outra pessoa como filtro.

Por padrão, lista treinamentos atribuídos em eventos `ATIVO` cuja conclusão
está pendente, rejeitada ou ainda não foi registrada. Filtros opcionais permitem
ver os concluídos ou todos os treinamentos atribuídos, incluindo eventos
encerrados e cancelados quando for solicitado o histórico completo. Cada item
mostra título, turma, início e término, status do evento e da participação,
NR vinculada, modo de conclusão, exigência de evidência e dados úteis disponíveis.
Os resultados são paginados (cinco por página, no máximo dez). Como as colunas
de horário do banco são `TIMESTAMP` sem fuso, a resposta não atribui UTC ou outro
fuso a essas datas.

Exemplo no chat: “Quais treinamentos eu preciso realizar?”. Essa consulta mostra
somente inscrições efetivas; uma NR obrigatória para o cargo não comprova que o
usuário já foi inscrito em uma turma. A tool não cria inscrição, conclusão ou
evento.

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

Campos auxiliares: `ultima_rota`, `acao_pendente`, `encerrada_em`, `resumo_parcial`
e `resumo_ate` para progresso do resumo; `lock_token` e `lock_ate` enquanto uma
operação reserva a sessão. Datas são BSON datetime em UTC. Um documento do formato básico, sem
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
- `403`: UID autenticado sem um dos quatro níveis de acesso reconhecidos.
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
resumo por trechos, repetição de encerramento, falhas parciais entre os bancos e a
obrigatoriedade de o guardrail respeitar avaliações negativas do Juiz.

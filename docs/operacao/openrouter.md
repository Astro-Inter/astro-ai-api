# OpenRouter: configuração, validação e migração

A API usa OpenRouter como única saída de inferência. A chave local foi configurada
em `.env`, ignorado pelo Git; esse arquivo é exclusivo do backend. Em outros
ambientes, forneça `OPENROUTER_API_KEY` pelo gerenciador de segredos da plataforma,
reinicie a aplicação e remova as variáveis antigas após o corte validado. Não
inclua o segredo em imagens, argumentos de build, URLs, frontend ou documentação.
O arquivo de origem da chave e o ZIP da aula não fazem parte do projeto.

## Modelos e contratos

| Responsabilidade | Modelo/endpoint |
| --- | --- |
| RH, SST, Agenda, FAQ, Orquestrador, Juiz, guardrails e resumo | `openai/gpt-6-luna`, `/api/v1/chat/completions` |
| Classificação fechada e seleção de especialista | `typesafe/jev-1.14`, `/api/alpha/decisions` |
| Documentos, consultas e memória | `openai/text-embedding-3-small`, `/api/v1/embeddings`, 1536 dimensões |

IDs e recursos foram conferidos no catálogo e em chamadas pequenas em 26/10/2026.
Luna anuncia JSON/structured outputs e reasoning; não anuncia temperature.
Enviamos `reasoning.effort=none`, `max_tokens=1600` e, quando solicitado,
`response_format=json_object`. Os schemas Pydantic e a correção local existente
continuam validando as saídas. Não há downgrade silencioso do modo JSON.
Não oferecemos ferramentas nativas ao provedor; uma resposta com `tool_calls` é
rejeitada. O LangGraph valida argumentos, autenticação, perfil, empresa, usuário e
confirmação antes de executar qualquer operação.

Referências oficiais: [catálogo de chat](https://openrouter.ai/api/v1/models),
[Luna](https://openrouter.ai/openai/gpt-6-luna),
[catálogo de embeddings](https://openrouter.ai/api/v1/embeddings/models),
[embeddings](https://openrouter.ai/docs/api/api-reference/embeddings/submit-an-embedding-request),
[Jev 1.14](https://openrouter.ai/typesafe/jev-1.14) e
[contrato Decisions](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).
Jev usa catálogo/página de decisões; sua ausência no catálogo de chat não implica
que deva ser enviado a chat/completions. Os três endpoints retornaram HTTP 200.

## Jev e a Etapa 7

O material do professor orientou o uso de `state` estruturado, rubricas fechadas,
`choice` e `noul` na mesma chamada. O programa interpreta números; Jev não redige
respostas, justificativas, `motivo`, `problemas` ou argumentos de ferramentas.
A integração permanece no OpenRouter, usando uma única chave de backend.

`OPENROUTER_JEV_ENABLED=true` habilita a seleção `rh/sst/agenda/faq/luna`.
A entrada contém até sete mensagens recentes pseudonimizadas, com orçamento de
8000 caracteres de histórico; o prompt extenso do roteador fica somente no Luna.
A rota especializada só é aceita quando escolha, probabilidade da escolha,
confiança e `noul` são válidos e os três sinais numéricos atingem 0,90. Esses
limiares são conservadores e precisam de calibração com o domínio do Astro.
Mensagens, notificações, acessos, histórico, confirmações, ambiguidades, PDFs e
esclarecimentos seguem para Luna. A decisão final permanece sujeita à normalização
e às verificações existentes da aplicação.

A API é **alpha**, sem assumir garantias da versão estável de chat. Não são usados
streaming, temperature, texto gerado ou ferramentas em Decisions. Campos adicionais
são ignorados; tipos, rótulos e distribuições esperados são validados. Não há
substituição por `jev-latest`. Erro/contrato alpha inválido abre circuito de 60s por
instância e usa Luna pelo OpenRouter. Baixa confiança também usa Luna. Chave
inválida e saldo insuficiente interrompem a inferência, pois afetam a conta inteira.
Desabilitar Jev conserva o roteador textual. A rubrica é interna; probabilidades
altas não comprovam acerto nem concedem permissões.

`OPENROUTER_EVIDENCE_CHECK_ENABLED=false` deixa preparada, mas desativada, a
checagem adicional inspirada na aula. Depois da revisão textual, respostas geradas
podem ser comparadas às fontes do turno com `choice=apoiada/parcial/sem_apoio` e
`noul`. Somente `apoiada` com sinais >=0,70 mantém o texto exatamente como estava.
Reprovação com confiança produz uma frase fixa de abstenção, impede histórico e
PDF desse turno e descarta prévias pendentes. Dúvida/falha alpha delega a verificação
do texto final ao Juiz Luna. O Juiz original continua produzindo motivos e problemas.
Respostas formatadas deterministicamente de ferramentas autorizadas conservam os
comprovantes e não recebem essa chamada adicional. Rejeitar uma frase não desfaz
uma operação: autorização e confirmação continuam ocorrendo antes da ferramenta.

Custo/latência: a classificação pode substituir uma chamada Luna em casos claros;
o fallback usa Jev + Luna. O teste real de Agenda preferiu fallback com o limiar
conservador. A checagem final acrescenta uma chamada Jev por resposta elegível e,
na dúvida, mais uma do Juiz. Por isso permanece desativada até avaliação rotulada;
não há repetição automática de Jev nem nova geração após abstenção.

## Erros e observabilidade

`openrouter.py` centraliza HTTP, segredo, timeouts e tratamento de falhas. Cada POST
normal permite no máximo duas tentativas; falhas transitórias 408/429/5xx elegíveis
podem repetir uma vez. `Retry-After` longo retorna indisponibilidade sem dormir na
requisição; o cliente recebe `retry_after` no limite de uso. 400, 401, 402, 403 e 404
não são repetidos. Respostas truncadas permitem uma nova geração, com orçamento de
4096 tokens. Assim, o caminho textual tem teto de quatro POSTs se ambos os ciclos
sofrerem erro transitório, além da correção de contrato já limitada no agente.
Respostas vazias, recusas sem conteúdo, ferramentas nativas e JSON inválido não
viram uma resposta aceita. O timeout global de chat continua em 120 segundos.

Logs incluem etapa, modelo, duração, status, tentativa, tokens e custo quando o
OpenRouter fornece `usage`. Não incluem headers, corpos, prompts, vetores ou textos
das exceções HTTP. Traces e pseudonimização existentes continuam ativos. Limites,
saldo insuficiente, modelo indisponível e configuração têm razões operacionais
separadas. Nenhum outro provedor de inferência é acionado.

## Inventário e plano de recuperação

Inventário somente leitura de 26/10/2026:

| Fonte | Existentes | Reindexáveis | Observação |
| --- | ---: | ---: | --- |
| `faq_chunks` | 8 | 8 | Texto, documento e página presentes; PDFs originais dispensáveis para reembutir os chunks existentes |
| `memoria_resumos` | 14 | — | Índice legado preservado; um ponto não tem resumo canônico atual |
| MongoDB `sessoes` | — | 13 | Resumos canônicos com UUID e dono válidos; sem divergência de dono com o índice antigo |

As únicas coleções vetoriais encontradas usam Mistral, 1024/Cosine. A migração gera
21 vetores novos. Após redação de identificadores, o teto conservador é de 10.658
tokens de entrada (bytes UTF-8), ou **US$ 0,00021316**, usando US$ 0,02/milhão de
tokens. É estimativa de inferência, sem armazenamento/transferência ou taxa de compra
de créditos. Reconfirme catálogo, fonte e preço antes de aplicar.

O novo índice de memória vem do MongoDB, incluindo resumos não indexados, sem
reintroduzir o ponto sem resumo canônico atual. Não se altera o MongoDB nem seus
flags de indexação. Histórico, mensagens e IDs são preservados. Se faltar texto,
página, documento, dono ou UUID, o relatório conta o impedimento e `--apply` aborta;
recupere o PDF ou o resumo canônico antes de prosseguir. Nenhum dado existente é
apagado pelo script.

## Procedimento manual de migração

1. Obtenha autorização específica para o ambiente/volume/custo. Mantenha a versão
   antiga e suas configurações disponíveis para retorno. Faça snapshots de Qdrant
   e backup do MongoDB pelo procedimento da plataforma, sem baixar dados ao Git.
2. Em uma janela de manutenção, suspenda ingestão e gravações de resumos/encerramento
   enquanto prepara e valida o corte. Evite concorrência entre dois operadores.
3. Execute apenas o inventário e confira a reconciliação, as fontes e o custo:

   ```powershell
   .venv\Scripts\python.exe -m app.scripts.migrate_vectors
   ```

4. **Somente após autorização**, execute com o volume confirmado e teto financeiro.
   Para o inventário acima, o comando preparado é:

   ```powershell
   .venv\Scripts\python.exe -m app.scripts.migrate_vectors --apply --expected-points 21 --max-cost-usd 0.001
   ```

   Os destinos padrão são `faq_chunks_openai_te3s_v1` e
   `memoria_resumos_openai_te3s_v1`. Eles devem ser novos; nomes existentes são
   recusados mesmo quando vazios. Para uma nova tentativa, use `--faq-target` e
   `--memory-target` com outro sufixo, contendo `_openai_te3s_`. O script cria
   1536/Cosine, índice de modelo e índice tenant `id_user`, gera lotes de até 50,
   preserva IDs/metadados e compara todos os IDs, payloads e contagens ao final.
5. Verifique as contagens, citações e páginas; valide busca e histórico com dois
   usuários/empresas distintos. Execute casos rotulados positivos, negativos,
   ambíguos, de esclarecimento e de confirmação. Não use só o score para aprovar.
6. Aponte `QDRANT_FAQ_COLLECTION` e `QDRANT_MEMORY_COLLECTION` aos destinos validados
   junto do segredo `OPENROUTER_API_KEY`, reinicie em homologação, teste e só depois
   autorize o corte de produção. Nenhuma troca automática ou deploy é executado.
7. Em falha, mantenha a versão anterior atendendo. Coleção nova parcial não é
   ativada nem apagada automaticamente. Para retorno após corte, restaure a revisão
   anterior e as configurações anteriores como conjunto. Esta versão recusa
   coleções antigas; apontá-la aos vetores Mistral não é rollback válido. Preserve
   também os destinos novos e reconcilie resumos posteriores à janela antes de
   uma nova tentativa; o MongoDB continua sendo a fonte canônica.

O script de PDFs também segue esse modelo: primeiro apenas extrai/estima; para
aplicar exige `--apply --expected-points N --max-cost-usd VALOR --target NOVA_COLECAO`.
Nunca substitui os pontos de uma coleção existente.

## Similaridade e avaliação

FAQ mantém top 5; memória mantém top 3 com filtro por UID e exclusão da sessão
atual. Todas as buscas exigem `modelo_embedding=openai/text-embedding-3-small` e
validam dimensão/Cosine. Nomes novos também impedem reutilizar coleções antigas.

Uma amostra de três chunks reais do FAQ e seis perguntas, sem gravar vetores,
produziu scores máximos positivos de 0,6995 / 0,6653 / 0,4151 e negativos de
0,2127 / 0,1350 / 0,0877. O limiar **0,35** separou os seis casos e permanece
provisório em `FAQ_SCORE_THRESHOLD`; essa amostra pequena não valida toda a base,
casos fronteiriços ou a qualidade semântica da memória entre usuários.

Após criar as coleções, prepare um JSONL **privado**, fora do Git, com `query` e
`expected_sources` (lista dos nomes dos PDFs, vazia para perguntas sem resposta).
A avaliação consulta sem gravar e compara precisão/recall em diferentes limiares:

```powershell
.venv\Scripts\python.exe -m app.scripts.calibrate_faq C:\dados-privados\casos.jsonl
```

Há no máximo 20 casos por padrão e um embedding por pergunta, mais a busca Qdrant.
Use perguntas reais autorizadas; não versione dados pessoais. Calibre Jev também
com intenção correta, possibilidade de rota direta e necessidade de esclarecimento;
os testes simulados comprovam o contrato, não a acurácia do classificador.

## Verificações locais e pendências

Os testes automatizados usam HTTP simulado e Qdrant em memória. Incluem os agentes,
roteamento, Juiz, guardrails, sessões, autorização, isolamento, PDFs, embeddings,
falhas, timeouts, tentativas, decisões inválidas e migração sem exclusão.
A suíte completa passou com 665 testes; depois, dois casos adicionais de isolamento
entre modelos passaram junto dos 62 testes de FAQ/memória (667 casos validados ao
todo). `git diff --check` e a busca pela chave nos arquivos destinados ao Git
passaram sem apontamentos.
O ambiente local é Python 3.14; produção/CI usam 3.12. `pip check` passou. Há avisos
das bibliotecas sobre Pydantic v1/Python 3.14 e de depreciação já presentes.
O Docker CLI está instalado, mas o daemon local estava inativo; build da imagem e
importação no Python 3.12 ficam para a CI/ambiente Docker disponível.

As chamadas reais foram pequenas: **11 requisições de inferência**, incluindo
smokes iniciais, adaptador, roteamento e uma amostra de similaridade. O `usage`
retornou custo total observado de **US$ 0,00058490**. Consultas aos catálogos e ao
inventário não geraram inferência. Não houve reindexação persistida nem deploy.

Antes de produção ainda é preciso autorizar a migração, validar os destinos com
casos de produto, configurar segredos/coleções no ambiente central astro-gitops,
confirmar o build CI e autorizar o deploy. A checagem final de evidência fica
optativa até calibração; as validações textuais existentes permanecem obrigatórias.

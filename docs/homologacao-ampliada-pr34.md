# Homologação ampliada da correção do chatbot

Complementa o [diagnóstico da PR #33](correcao-producao-pr33.md). Em 01/11/2026,
produção continuava na revisão `3ad06be7bf696bac04d954439e63fd8be5d66c79`. A PR #34
contém correções posteriores; os testes abaixo não representam deploy. O anexo
da homologação web foi tratado como relato de testes, sem alterar o frontend.

## Falhas confirmadas e correções

| Caso | Evidência e correção |
| --- | --- |
| 500 em matriz/cobertura | Datas Python eram incompatíveis com BSON; persistência dos blocos em JSON mantém datas ISO. Reprodução e regressão com codificador BSON real. |
| HTTP 200 só com resumo | Saída determinística ignorava veredito negativo, mas montagem exigia aprovação, permitindo texto de sucesso sem componentes. Agora revisar/rejeitado produz mensagem explícita de não validação, sem turno aprovado; falha do revisor produz 503. Nenhuma aprovação é fabricada. |
| Mudança explícita de pergunta | Roteamento dependia da inferência do modelo no histórico. Intenções exatas de leitura da homologação agora são reconhecidas pelo backend depois do guardrail, com filtros e quantidade da mensagem atual. Fora da gramática limitada, mantém roteamento normal; não aceita privilégios declarados. Não foi provado replay indevido de checkpoint na produção. |
| Tabela → gráfico | Recupera os filtros da própria sessão e consulta a agregação completa, preservando também o critério cadastral de UID da listagem. Não calcula sobre linhas nem páginas visuais. |
| Linhas pessoais zeradas | Tool retornava sem_dados apesar de construir 12 meses válidos. Série completa passa a ser ok, inclusive todos zero. Plano visual é enviado ao Juiz. Evolução de equipe continua omitindo meses ausentes. |
| Conflitos vazios | Resumo citava gráficos ausentes e não respondia diretamente sobre sobreposição. O Juiz real pediu revisão. Texto corrigido descreve período/escopo e ausência de pares; não promete componentes ausentes. Nova execução aprovada. |
| Seleção de evento | A SQL não selecionava evento.id_evento. Listagem agora mostra referência e a sessão mantém título/referência do conjunto autorizado. Seleção exata e inequívoca consulta novamente a API Java; nomes iguais pedem referência. |
| Link localhost | Consulta real de eventos encontrou dois links localhost vindos de evento.link_externo. A aplicação não constrói esse endereço nesse fluxo; não foi reescrito. Correção cadastral depende do responsável pelos eventos. |
| Normas/FAQ | Catálogo Qdrant local acessível, mas a coleção configurada faq_chunks_openai_te3s_v1 não existe entre as três coleções. get_collection respondeu 404 antes de qualquer embedding. Não confirma os valores das configurações runtime da produção, que não estavam acessíveis. Não houve fallback para vetores antigos ou migração automática. |

O pipeline não troca uma falha SQL/Java/provedor por conjunto vazio. Logs de erro
analítico/FAQ agora incluem tipo/código/frames controlados, sem mensagem livre de
driver, query, credenciais ou URLs. Timeouts e limites não foram aumentados.
Falhas de dependência continuam explícitas. Sem logs do runtime, não se atribui
toda intermitência relatada a uma única causa nem se afirma ter corrigido CORS,
proxy ou credenciais de produção. `/health` não verifica as dependências.

## Leituras reais no serviço local corrigido

Modelos reais via OpenRouter e leituras dos cadastros autorizados; histórico em
memória com validação BSON antes de toda atualização. Não foi usado Mongo remoto
para esses turnos. Fonte, unidade, filtros e permissões permaneceram no backend.

| Sequência/consulta | Resultado observado |
| --- | --- |
| Cobertura | Indicadores + tabela + barras + contexto; seis NRs, 197 pares sem encaminhamento no retrato consultado |
| Matriz após cobertura | Indicadores + matriz + contexto; 100 de 272 pares; sem reaproveitar resumo de cobertura |
| Comparação após matriz | Indicadores + tabela + barras + contexto; três unidades autorizadas |
| Listagem após comparação | 100 de 141 usuários; tabela local |
| Novo pedido de 500 na mesma conversa | 141 de 141, sem corte; não inventou 500 cadastros nem pediu página ao chatbot |
| Barras baseadas na listagem | Quatro categorias somando 141, independentemente das 100 linhas entregues |
| Linhas pessoais | 12 categorias, inclusive zeros, bloco grafico_linhas e igualdade no histórico |
| Conflitos próprios | Zero pares no período; após correção do resumo, Juiz aprovado, indicadores/contexto e igualdade no histórico |
| Evidências do criador | Zero envios no escopo; indicadores/contexto e igualdade no histórico |
| Eventos próprios | Quatro participações listadas com referência numérica; links de origem preservados |

Os valores são retratos, não constantes do produto. Não foram criados usuários,
eventos, turmas, inscrições ou evidências. Fila real não vazia continua sem dados
e depende de ocorrer legitimamente; casos positivos são simulados. Resumo de evento
por nome/referência e acesso negado foram verificados no pipeline com Java simulada;
não se afirma homologação real de todos os eventos pela API Java.

Soma das rodadas locais desde o início da correção: **44 inferências medidas,
166.304 tokens e US$ 0,021496507**, conforme usage do OpenRouter. As quatro
reproduções HTTP no ambiente publicado não retornaram usage ao cliente; seu
consumo não integra essa soma. Não houve embeddings reais nesta investigação.
As várias rodadas serviram para descobrir e corrigir o estado sem_dados das linhas
e a revisão negativa do conjunto vazio, não para contornar o Juiz.

## PDF e clientes

Novo [bloco arquivo v1](arquivos-chat.md) entrega nome, UUID opaco, MIME, tamanho
opcional, URL temporária, expiração e estado. Texto e arquivo são blocos distintos.
Mesmo contrato no POST e histórico; recuperação nunca renova o prazo. URLs ficam
no mapa privado da sessão, não em texto/blocos persistidos ou prompts/resumos.
Esta extensão foi testada com armazenamento e geração simulados; não houve upload
real. Web/mobile precisam implementar esse discriminador antes de publicar a API.

## Dependências restantes

- Autorizar merge e deploy, confirmar SHA em /version e repetir homologação do
  endpoint publicado; não houve publicação nesta tarefa.
- Suporte web/mobile a arquivo v1 e estados de expiração, junto dos blocos da PR #33.
- Confirmar a configuração Qdrant da produção. No ambiente local, falta
  faq_chunks_openai_te3s_v1. É necessário inventariar coleções, fontes originais e
  volume, estimar custo e apresentar recuperação antes de qualquer reindexação.
  A execução não foi autorizada por este diagnóstico e nenhum dado foi apagado.
- Corrigir links localhost no cadastro de origem, mediante autorização específica.
- Consultar logs/métricas do runtime e request IDs das falhas intermitentes para
  atribuir os demais erros de provedor/infraestrutura. Os scripts locais não têm
  acesso ao contexto Kubernetes remoto; não substituem essa evidência.

As regressões cobrem o endpoint completo, mudanças de intenção na mesma sessão,
aprovação/rejeição/indisponibilidade, histórico, vazios, limites e paginação local,
linhas/rosca zeradas, autorização de eventos, arquivos, expiração e privacidade da
memória. Validação final: **1.037 testes passaram**, com 15 avisos de dependências. Após pequenos ajustes de validação de links e Retry-After, as suítes focadas de entrega, replay e PDF também passaram. Compileall, diff --check, exemplo JSON de arquivo e comparação com segredos locais passaram. A descrição da PR registra o resultado da CI.

# Homologação de PDF e contexto de gráficos — novembro de 2026

## Revisão e limites da investigação

`GET https://ia.astro-app.solutions/version` retornou `bf2f90998aba17d8b3480cfcbb6b0f65039b2f76`. A investigação e as correções foram preparadas em worktree isolado dessa revisão, preservando alterações locais de FAQ, MongoDB, sessões e memória. Não houve merge, deploy, troca de configuração remota, reindexação ou chamada de LLM/embeddings nesta investigação.

Os testes de produto usam dados e modelos simulados. As leituras reais limitaram-se a versão pública, metadados Qdrant, uma consulta vetorial sintética sem payload e manifests GitOps. Não foram usados nem copiados para scripts o Bearer fornecido pelo usuário, mensagens de outras contas, credenciais ou URLs assinadas.

## PDF sobre o Astro: erro anterior ao arquivo

**Atualização posterior:** após a imagem do usuário, nova leitura encontrou apenas `faq_chunks` e `memoria_resumos`. A coleção OpenAI abaixo não está mais presente. As evidências seguintes descrevem a primeira leitura; o ajuste final solicitado usa OpenAI/1024 para FAQ e está em [FAQ OpenAI 1024](faq-openai-1024.md). Não executar a antiga orientação de corte para o destino ausente.

A imagem informa HTTP 503, `Consulta as normas indisponivel.`, request ID `e4a516ed-b4e8-4679-aa21-a6d0d124bb86`, em 01/11/2026 às 17:09:57 UTC. Na revisão publicada, esse detalhe é produzido por `FaqVectors`, não pelo renderer ou pelo upload R2. O fluxo consulta fontes para construir a explicação, depois passa pelo Juiz e guardrail e somente então gera o PDF. Portanto esse erro não comprova falha de armazenamento, download ou CORS.

Evidências reais sanitizadas:

- A configuração local aponta para `faq_chunks_openai_te3s_v1`. Essa coleção retorna 404 no Qdrant configurado e não consta na listagem de coleções. Os exemplos Render/Kubernetes da mesma revisão também apontavam para esse nome.
- `faq_chunks_openai_te3s_20261027` existe com 8 pontos, vetor de 1536 dimensões/Cosine e índice keyword de `modelo_embedding`. Uma busca sintética somente de leitura com o filtro `openai/text-embedding-3-small` funcionou. Nenhum texto de documento foi exibido. Essa coleção já estava documentada como migrada e calibrada em `docs/operacao/openrouter.md`.
- O GitOps de Academy aponta para a mesma revisão da API. Seu ConfigMap não declara `QDRANT_FAQ_COLLECTION`; se não houver override efetivo por Secret ou outro mecanismo, o código usa o default `faq_chunks_openai_te3s_v1`. Isso é evidência de divergência de configuração, mas não substitui a leitura do ambiente efetivo do pod.
- Não foi possível correlacionar o request ID nos logs AWS: o contexto kubectl disponível é somente docker-desktop, sem cluster ativo; o acesso ao console por Computer Use foi interrompido pela ferramenta porque não conseguiu confirmar a URL atual. Não atribuímos ao pod uma variável que não foi lida.

Correções preparadas:

- Diagnóstico `faq_search_failed` identifica `faq_metadata`, `faq_query`, `faq_embeddings` ou `faq_payload`, com status HTTP controlado. Um 404 na consulta de metadados gera `FAQ_COLLECTION_MISSING` e reason `faq_collection_missing`. Um 404/400 na busca não é confundido com coleção ausente na etapa de metadados. Não registra corpo de erro, URL do serviço, texto da pergunta ou credenciais.
- Erro de embeddings preserva o tratamento específico do OpenRouter; não adiciona inferências ou retries. A resposta pública de erro continua sanitizada e compatível.
- A proposta inicial de apontar os exemplos para a coleção migrada foi substituída: default/exemplos agora usam `faq_chunks`, com FAQ OpenAI em 1024 dimensões e verificação de modelo. Nenhuma coleção ou vetor remoto foi alterado.
- Regressão do POST confirma que FAQ indisponível impede upload, cartão de sucesso e persistência de turno incompleto. O fluxo de PDF aprovado e o contrato arquivo v1 permanecem inalterados.

### Ação operacional pendente

**Orientação inicial substituída; consultar o documento FAQ OpenAI 1024 antes de qualquer corte.** Um operador autorizado deve confirmar a variável efetiva `QDRANT_FAQ_COLLECTION` e o erro correlacionado no runtime AWS. Após revisão/homologação, configurar explicitamente `QDRANT_FAQ_COLLECTION=faq_chunks_openai_te3s_20261027` no ambiente que atende o domínio, garantindo que um Secret antigo não sobrescreva o ConfigMap. O consumidor mantém o mesmo modelo e filtro; não migrar ou apagar vetores. A alteração dos exemplos nesta PR não atualiza o GitOps nem o ambiente remoto.

Repetir a busca documental e o pedido de PDF após uma publicação autorizada. Só considerar sucesso quando retornar arquivo v1 e o download for validado. Não renovar assinaturas existentes para testar. A validade/custo da configuração anterior não é restaurada apontando o modelo novo para vetores legados: qualquer recuperação deve manter código/modelo/coleção compatíveis como conjunto.

## Barras com base na listagem

A frase `Faça um gráfico de barras por cargo com base nessa listagem.` não correspondia à expressão do reconhecimento contextual, que exigia `com base nesses dados` ou `desses dados`. A reprodução automatizada com uma sugestão de recusa do roteador falhou antes da correção para `nessa listagem` e `nesta listagem`; a formulação antiga funcionava. Isso explica uma limitação concreta de reconhecimento, sem afirmar que todo o histórico foi perdido ou atribuir o problema ao frontend.

O reconhecimento passa a aceitar `nessa listagem`, `nesta listagem` e `na listagem anterior`. Continua exigindo referência explícita e limitada, última rota RH e filtros estruturados válidos salvos pela aplicação. Reaplica tipos, status, nome e cargo ao agregado, e a tool revalida a conta e o escopo atual. Não reaproveita limite/página nem calcula a distribuição a partir das linhas recebidas. Sem filtros confiáveis, pede esclarecimento; não amplia a consulta.

Teste: histórico sintético de 100/141, agregado autorizado 141, comparação entre POST/histórico e recusa de entrada/Juiz. A recusa de qualquer barreira não é convertida em aprovação nem em gráfico visual. Paginação continua exclusivamente local.

## Evolução mensal pessoal

`Mostre minha evolução mensal em um gráfico de linhas.` identifica pessoa e periodicidade, mas não o indicador. Não implica automaticamente treinamentos, conformidade, presença ou desempenho. Após uma listagem de equipe, também não deve herdar o escopo coletivo.

O roteador passa a produzir um esclarecimento específico sobre o indicador pessoal, oferecendo conclusões de treinamentos como opção. Não afirma que dados estão ausentes antes de consultar uma tool e não gera gráfico sem métrica definida. A entrada, Juiz e guardrail de saída continuam ativos. O esclarecimento segue o formato de texto já existente; o histórico mantém o conteúdo textual compatível com clientes legados.

Para pedir a série já disponível, usar `Mostre um gráfico de linhas dos meus treinamentos concluídos.` ou `Mostre a evolução mensal das minhas conclusões em um gráfico de linhas.` O fluxo pessoal consulta somente o UID autenticado e os 12 meses previstos na tool, preservando zeros. A clarificação não cria meses ou valores nem escolhe um indicador por conta própria.

## Roteiro de homologação web/mobile

1. `Liste 100 usuários da minha equipe em uma tabela.`
2. Na mesma sessão: `Faça um gráfico de barras por cargo com base nessa listagem.` Conferir o agregado completo autorizado, filtros e histórico, sem chamada adicional por paginação visual.
3. Repetir com `com base nesta listagem` e `com base nesses dados considerando todo o escopo autorizado`.
4. `Mostre minha evolução mensal em um gráfico de linhas.` Deve perguntar o indicador, sem inventar gráfico nem dizer que não há dados.
5. `Mostre um gráfico de linhas dos meus treinamentos concluídos.` Deve consultar os dados pessoais e preservar os 12 meses/zeros quando aplicável.
6. Após corrigir a configuração FAQ no runtime autorizado: `Gere um PDF explicando o que é o Astro`. Conferir fonte, aprovação, arquivo v1 e download; em indisponibilidade não pode existir cartão de sucesso.

Nenhuma mudança de schema/bloco foi introduzida. Web/mobile não precisam compensar o reconhecimento de linguagem alterando dados ou permissões. A correção precisa estar publicada para os testes reais refletirem este código; conferir `/version` antes da homologação.

## Verificação final

A suíte completa local passou: **1097 testes**, com 15 avisos de dependências. A reprodução anterior à correção teve quatro falhas nas duas novas formulações de referência à listagem; a rodada focada corrigida passou (10 testes), além dos 34 testes de FAQ/diagnóstico. `git diff --check` e a comparação dos arquivos preparados com credenciais locais passaram. CI e revisão da PR devem ser verificados na revisão enviada; não foi publicada uma nova versão do runtime.

# Correção das coleções originais — 01/11/2026

> Atualização posterior do conteúdo: `faq_chunks` tem agora 46 trechos do FAQ funcional v2.0, mantendo OpenRouter/OpenAI, 1024/Cosine e o nome original. As contagens abaixo documentam o reparo anterior; consulte o [relatório da atualização do FAQ](atualizacao-faq-funcional.md) para fontes, estado atual e recuperação.

Decisão explícita do usuário: manter exatamente `faq_chunks` e `memoria_resumos`. A preparação anterior em coleções separadas não é a configuração final desejada.

## Reparo realizado no Qdrant

Antes de sobrescrever vetores, foram criados snapshots nativos:

- `faq_chunks-7873424337948012-2026-11-01-21-10-48.snapshot` (165376 bytes).
- `memoria_resumos-7873424337948012-2026-11-01-21-10-48.snapshot` (359936 bytes).

Os embeddings OpenAI/1024 já gerados e validados foram reaproveitados, sem novo custo de IA. Fontes canônicas, fingerprints, donos e IDs foram conferidos novamente antes da escrita. Nenhuma coleção foi apagada ou recriada; a configuração do vetor nomeado vazio foi preservada.

`faq_chunks` mantém 8 pontos, agora `openai/text-embedding-3-small`, 1024/Cosine. Textos, fontes, páginas e IDs foram preservados. `memoria_resumos` contém 14 resumos canônicos reindexados pelo mesmo modelo e 1 ponto legado sem correspondente canônico preservado: 15 pontos físicos, 14 válidos para as buscas OpenAI. O legado não foi renomeado artificialmente como OpenAI e continua excluído pelo filtro de modelo. A contagem física não deve ser apresentada como quantidade de resumos atuais da conta.

IDs, payloads, vetores e união das contagens esperadas foram verificados após escrita. Pontos não promovidos foram comparados integralmente com a origem e permaneceram intactos. A consulta filtrada de memória foi testada para todos os donos canônicos, sem expor dados pessoais. Índices keyword `modelo_embedding` existem nas duas coleções e o índice de usuário foi preservado. Não houve exclusão de dados nem nova coleção nesta etapa. Os destinos intermediários anteriores não foram ativados ou apagados.

## Ajuste de aplicação

O código publicado na revisão `0a8cd90` já usa FAQ/1024 pelo OpenRouter, mas a classe de memória ainda rejeita nomes sem `_openai_te3s_`. Essa exigência de nome é removida; a validação efetiva 1024/Cosine e os filtros obrigatórios por `id_user`, modelo e exclusão da sessão atual são preservados. Default, `.env.example`, Render e exemplo Kubernetes passam a `QDRANT_MEMORY_COLLECTION=memoria_resumos`. FAQ continua `faq_chunks`.

O teste com Qdrant em memória usa expressamente o nome `memoria_resumos` e verifica escrita, atualização, isolamento entre usuários e exclusão da sessão atual. A correção de código é entregue para revisão, sem merge ou deploy nesta etapa. Para a memória funcionar na aplicação publicada é necessário publicar essa correção com autorização; alterar apenas os dados Qdrant não remove uma validação do código em execução.

## Recuperação e limites

Os snapshots anteriores preservam vetores e payloads originais. Não restaurar automaticamente em cima de escritas novas: revisar o período e o estado do Mongo antes de qualquer recuperação; manter código/modelo e dados compatíveis como conjunto. As coleções intermediárias permanecem disponíveis, mas não são destinos ativos. Não remover snapshots nem coleções sem solicitação explícita.

A operação de Qdrant não demonstra geração de PDF pelo pipeline completo. Validar FAQ/PDF e memória com conta autorizada após publicar a correção, preservando Juiz/guardrails. Nenhuma chave, URL assinada, texto de resumo ou identificação pessoal foi registrada neste documento.

Validação desta correção: 72 testes de memória e FAQ passaram, com 3 avisos de dependências. A leitura/gravação nos nomes originais, os filtros por usuário/modelo e a exclusão da sessão atual foram exercitados com dados simulados. Diff --check passou. As verificações remotas de Qdrant acima conferiram os dados reais sem registrar conteúdo privado. O teste de PDF completo em produção não foi executado nesta etapa.

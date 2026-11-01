# FAQ e memória OpenAI em 1024 dimensões

A pedido do usuário, FAQ usa `openai/text-embedding-3-small` pelo OpenRouter com `dimensions: 1024`. A memória passa a usar o mesmo modelo e também 1024 dimensões em geração, gravação e busca. Nenhum vetor remoto foi convertido ou alterado. Consultas FAQ, ingestão de PDFs e migração dos textos FAQ usam o wrapper `gerar_embeddings_faq`, que exige resposta com exatamente 1024 números finitos não nulos. O helper genérico também exige 1024 para a memória. O modelo remoto não pode ignorar a dimensão e devolver 1536 sem que a resposta seja rejeitada.

O default de `QDRANT_FAQ_COLLECTION` e os exemplos de ambiente passam a `faq_chunks`. A leitura aceita esse nome sem exigir o sufixo da antiga migração, mas valida dimensão 1024/Cosine e metadado `modelo_embedding=openai/text-embedding-3-small`. O filtro permanece em cada busca. Não há troca automática para outra coleção ou para outro provedor.

## Impedimento atual, confirmado em leitura

A imagem e a nova leitura Qdrant mostram `faq_chunks` com 8 pontos, 1024/Cosine, mas os metadados dizem `modelo_embedding=mistral-embed`. O outro índice OpenAI visto na investigação inicial não está mais presente. Igualar dimensões não converte os vetores: coordenadas Mistral e OpenAI representam espaços diferentes. Alterar apenas a etiqueta do payload também não converte nada.

O código pede 1024 ao OpenAI, mas **não pode consultar semanticamente os vetores Mistral como se fossem OpenAI**. Ao encontrar apenas pontos legados, retorna indisponibilidade com reason interno `faq_model_incompatible` antes de gastar uma chamada de embedding. Não retorna uma falsa ausência de documentos e não envia vetor incompatível ao Qdrant. Se houver pontos OpenAI válidos na coleção, o filtro exclui os demais; não se afirma cobertura integral de uma reindexação parcial.

Nenhuma coleção foi apagada, sobrescrita, recriada ou migrada. Para usar de fato o conteúdo atual com OpenAI é necessário regenerar seus vetores a partir dos textos, com um procedimento de reindexação e recuperação revisado. Preservar dados existentes é requisito anterior do projeto. Reutilizar fisicamente o nome `faq_chunks` numa troca exige um plano próprio e autorização; não é realizado automaticamente por esta PR.

## Preparação opcional da reindexação — sem execução remota

O script `app.scripts.migrate_faq` foi preparado para ler apenas texto/fonte/página do FAQ, sem depender de MongoDB/memória. Sem `--apply`, não cria coleção, não chama IA e não escreve no Qdrant. Usa os textos originais; não tenta transformar vetores numéricos Mistral em OpenAI.

```powershell
python -m app.scripts.migrate_faq --source faq_chunks --target faq_chunks_openai_te3s_1024_v1
```

O inventário real limitado encontrou 8 trechos, teto conservador de 5131 tokens e US$ 0,00010262 a US$ 0,02/milhão de tokens (catálogo OpenRouter conferido em 01/11/2026). Isso é estimativa de entrada, não consumo já cobrado de migração. Reconfirmar catálogo, inventário e custo antes de aplicar. A fingerprint do planejamento deve ser repetida em `--source-fingerprint`, junto de `--expected-points` e `--max-cost-usd`. Não executar esses parâmetros em produção sem aprovar o plano.

O destino precisa ser novo e conter `_openai_te3s_`; o script recusa fonte=destino, coleção existente, texto/fonte/página ausente, mais de 10000 registros, drift de fingerprint, volume ou teto divergente. Após escrita, valida IDs, payload e contagem. Falha deixa o destino parcial separado e não muda a configuração. A origem continua intacta para recuperação. Não ativa coleção nem modifica permissões. Não apagar dados para liberar o mesmo nome.

A operação conjunta existente `migrate_vectors` também foi alinhada: destino FAQ 1024, memória 1024, com geradores respectivos. Sua execução continua sujeita ao inventário e autorização já documentados. Não usá-la para contornar pendências da memória.

## Homologação antes de usar dados reindexados

O limiar 0,35 e top 6 existentes não foram baixados. A calibração antiga de 1536 não comprova qualidade em 1024. Antes do corte autorizado, repetir perguntas positivas/negativas do documento, cobertura de citações/páginas, PDF e revisão do Juiz. Validar os dados reais, não apenas a dimensão. Nenhuma resposta documental ou PDF deve ser liberado sem evidência e aprovação.

Foi feita uma única confirmação paga mínima com frase pública: o OpenRouter retornou exatamente **1024 dimensões**, 11 tokens, custo observado **US$ 0,00000022**. Nenhum vetor foi gravado. Os demais testes usam mocks/Qdrant em memória. Não houve deploy, merge, alteração de `.env` privado ou assinatura de PDF. O transporte de ambas as funções é exclusivamente o OpenRouter; referências Mistral nesta documentação descrevem metadados históricos dos pontos, não um provedor ativo no código. Sobrescritas efetivas em AWS/Render ainda devem ser verificadas na publicação autorizada.

Documentação oficial: [API de embeddings](https://openrouter.ai/docs/api-reference/embeddings), [catálogo do modelo](https://openrouter.ai/openai/text-embedding-3-small).

Validação final: **1106 testes passaram**, com 15 avisos de dependências; 57 testes focados de FAQ/embeddings/migração passaram. Compileall, diff --check e varredura dos arquivos preparados contra segredos locais passaram. A confirmação real não substitui a calibração e reindexação pendentes.

A configuração de destino da memória permanece explícita em `QDRANT_MEMORY_COLLECTION`. A coleção remota `memoria_resumos` da imagem tem 1024 dimensões; o código agora tem o mesmo tamanho, mas não troca destinos nem reutiliza automaticamente pontos de modelo desconhecido. Continuam valendo o filtro obrigatório por `id_user`, exclusão da sessão atual e modelo de embedding, e a fonte canônica MongoDB. A migração e reconciliação desses resumos continuam separadas do ajuste de dimensão.

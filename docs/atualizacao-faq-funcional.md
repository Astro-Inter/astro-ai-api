# Atualização do FAQ funcional — 01/11/2026

## Entrega e fontes

O FAQ anterior era uma instrução genérica de duas páginas, sem detalhar os fluxos
do produto e com afirmações que as fontes atuais não sustentam (por exemplo,
ausência de compartilhamento com terceiros). Sua data de vigência futura não foi
reaproveitada como aprovação jurídica. A nova versão é um guia funcional de
**46 perguntas, 10 páginas e 46 trechos independentes**, não nova política legal.

- [Fonte editável](faq/astro-faq-funcional.md).
- [PDF revisado](../output/pdf/Astro_FAQ_Funcional_v2.0.pdf).
- Fonte principal: astro-knowledge, revisão `f051f16014f1b85d7d9bc7d02f37e347dacfd097`.
- O memorial completo e todas as notas funcionais do índice foram consultados.
  O memorial local coincide com o blob remoto `93f3e0806bc1252b9ebfb55218b64b1aa8a7f3a2`.
- O índice distingue definição funcional de implementação. Recursos não são
  apresentados como publicados apenas porque constam no memorial. Pendências
  de indicadores, Notificações, geofence e detalhes de cancelamento continuam explícitas.
- Contato `app.4str0@gmail.com`, segunda a sexta, 07h–18h, revalidado pelo
  responsável pelo produto em 01/11/2026. Não se deduz SLA, telefone ou feriados.
- Contratos complementares de PDF, perfil e consultas: astro-ai-api `0d50610`.
  Não se afirma que as alterações ainda em PR já estejam publicadas.

| Referência | Fonte consultada |
| --- | --- |
| K1 | [Memorial integral](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/fontes/memorial-descritivo-funcional-2026-09-23.md) |
| K2 | [Índice e visão geral](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/README.md) |
| K3 | [Workspace e papéis](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/acesso/workspace-e-papeis.md) |
| K4 | [Cadastro e importação](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/colaboradores/cadastro-e-importacao.md) |
| K5 | [NRs e validade](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/conformidades/nrs-e-validade.md) |
| K6 | [Eventos, Turmas e evidências](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/eventos/eventos-turmas-e-evidencias.md) |
| K7 | [Formulários e inspeções](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/operacao/formularios-e-inspecoes.md) |
| K8 | [Comunicação e interfaces](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/operacao/comunicacao-e-interfaces.md) |
| K9 | [Análise de NRs e cálculos](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/ia/analise-de-nrs-e-calculos.md) |
| K10 | [Pendências](https://github.com/Astro-Inter/astro-knowledge/blob/f051f16014f1b85d7d9bc7d02f37e347dacfd097/docs/astro/decisoes/pendencias-e-evolucao.md) |

## Operação autorizada no Qdrant

A operação atualizou exclusivamente a coleção existente **faq_chunks**. O modelo
continua `openai/text-embedding-3-small`, pelo OpenRouter, com **1024 dimensões e
Cosine**. Não houve criação/troca de coleção ou intervenção em memoria_resumos.

| Item | Resultado final |
| --- | --- |
| Pontos anteriores | 8 |
| Pontos atualizados, IDs originais preservados | 8 |
| Pontos novos | 38 |
| Pontos finais | 46 |
| Pontos originais excluídos | 0 |
| Fonte ativa | Astro_FAQ_Funcional_v2.0.pdf |
| Tamanho máximo de trecho | 499 caracteres |
| Threshold / limite de busca testados | 0,35 / 6 |
| Snapshot anterior à aplicação final | `faq_chunks-7873424337948012-2026-11-01-22-46-29.snapshot` |
| Tamanho do snapshot | 1052160 bytes |

Não se manteve o texto antigo conflitante como outra fonte ativa. O conteúdo
anterior, vetores e payloads foram preservados em snapshot nativo e em cópia local
`tmp/faq-update/inventory-original.json`, fora dos commits. O PDF original também
não foi sobrescrito. Os oito IDs antigos receberam as primeiras oito perguntas;
os demais IDs são determinísticos para esta versão da fonte.

Cada trecho contém a pergunta, resposta, referência, fonte pública e página
**zero-based** do PDF, além de faq_id, versão, hash da fonte e revisão do conhecimento.
A API continua apresentando páginas **one-based** ao usuário, conforme seu contrato.
O payload não inclui caminhos locais, chaves, resumos de contas ou cadastros reais.

Fingerprint anterior: `db0e3b42567698a265556704ffbd1dc30aa82b7b399765ac3f6714f4d3edcf66`.

Fingerprint final: `28a2dc6079a8cced8252d5ae1cf9074c804160e3406ece6d7fe6ddf84481e95d`.

SHA-256 do Markdown indexado: `5eddf282a6a8d8251de0c24b1cacca42780b1086f1b9b2902fe32351b8422914`.

SHA-256 do PDF entregue: `287fb1695c8b8c078e1d8587ba321b5bace549310b873655e27810adcc580e85`.

## Validação e consumo

- **63 testes locais passaram**: construção do documento, correspondência exata
  de respostas/páginas e regressões de FAQ, ingestão e migração, com modelos simulados.
- Dez páginas renderizadas e revisadas visualmente. Cada pergunta e sua resposta
  permanecem juntas; a extração foi comparada com o Markdown antes da indexação.
- Todos os payloads e IDs foram relidos e comparados após a gravação. Vetores
  conferidos com a normalização Cosine, dimensão e precisão float32 esperadas.
- **8 consultas reais de recuperação aprovadas**: produto, evidências, Cargo/Unidade,
  primeiro acesso, suporte, Chat, limites do chatbot e uma pergunta fora de escopo.
  As sete positivas recuperaram o FAQ esperado em primeiro lugar; a negativa não
  retornou resultado acima de 0,35. Não se baixou o threshold para fazer testes passarem.
- Não houve chamada real de geração de texto, PDF pelo chatbot ou homologação
  completa de Luna/Juiz nesta operação. A validação real comprova recuperação,
  não garante a resposta final de todos os agentes ou de todas as versões publicadas.

O plano apresentado antes da execução previa até 46 trechos e teto inferior a
US$ 0,001. O [catálogo oficial](https://openrouter.ai/openai/text-embedding-3-small)
confirmava US$ 0,02 por milhão de tokens.

Consumo observado no campo usage do OpenRouter: **5.470 tokens, US$ 0,00010940**
em três chamadas de embeddings (5.254 + 108 + 108 tokens). A primeira gerou os
46 documentos e 8 consultas; as duas seguintes repetiram somente consultas.
Os embeddings de documentos foram reaproveitados.

Durante a validação inicial, o SDK retornou o vetor padrão como lista e o Qdrant
normalizou os vetores na métrica Cosine. As comparações iniciais, que esperavam
um dicionário e igualdade com os valores não normalizados, interromperam a
operação. As duas tentativas restauraram integralmente os oito registros
anteriores, conferidos por fingerprint; apenas os pontos temporários criados
por cada tentativa foram removidos na recuperação. O procedimento final compara
os valores normalizados com tolerância de 1e-6 e verifica norma unitária. Isso
segue a [normalização documentada pelo Qdrant](https://qdrant.tech/documentation/manage-data/collections/).
A aplicação do chatbot não foi alterada para contornar esse comportamento.

## Reconstrução e futuras atualizações

```powershell
python -m app.scripts.build_product_faq
```

Esse comando é **offline**: gera o PDF e registros de citação em
`tmp/faq-update/records.json`. Não acessa OpenRouter nem grava Qdrant. Requer as
dependências já declaradas no projeto. Execute da raiz do repositório. Alterar o
Markdown não atualiza a coleção automaticamente.

A atualização remota foi uma operação administrativa delimitada: inventário,
backup e snapshot, estimativa, embeddings, conferência de qualidade e fingerprint,
upsert no nome existente, releitura e consultas reais. Não use o script genérico
`ingest_faq` para sobrescrever essa coleção; ele continua exigindo coleção nova.
Qualquer nova atualização deve inventariar novamente o estado e revisar o volume,
custo, IDs, diferença de conteúdo e plano de recuperação antes de aplicar.

## Recuperação

O snapshot e a cópia local preservam o estado anterior. Não restaure cegamente
sobre alterações posteriores. Primeiro inventarie a coleção e compare a revisão
que se pretende desfazer. Preserve nova cópia antes de uma recuperação.

Para desfazer somente esta versão, restaure os oito IDs e seus vetores/payloads
da cópia original; remova somente os 38 IDs adicionados por esta operação depois
de confirmar que ainda pertencem à mesma versão/hash. Não remova pontos de outra
origem ou atualização posterior. A lista exata de IDs consta nas cópias locais e
no snapshot, não no histórico público do chat. Confira a igualdade do fingerprint
anterior e teste a busca com o mesmo modelo. Restauração de snapshot da coleção
inteira também exige revisar as escritas posteriores e o procedimento nativo.

A carga solicitada já está efetivada no Qdrant. Arquivos e documentação seguem
pela PR, sem merge ou deploy do código da aplicação. O novo conteúdo não depende
de deploy quando o runtime já consulta faq_chunks com o mesmo modelo/dimensão.

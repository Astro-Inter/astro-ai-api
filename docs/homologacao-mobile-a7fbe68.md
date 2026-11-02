# Correções da homologação mobile sobre a7fbe68

## Revisão e limites da investigação

Em 01/11/2026, `/version` confirmou
`a7fbe680520c90355f17f766fb6d21efce4e8f38`, merge da PR #34. Não havia commits
posteriores na main no início da investigação. Alterações foram feitas em branch
separada, preservando o checkout original e o trabalho já publicado. Sem merge ou
deploy nesta tarefa. Não houve alteração no Android, payload para compensar escala
de gráficos, permissões, cadastros ou contagens.

Não havia acesso aos logs do runtime remoto no contexto configurado. As evidências
abaixo distinguem reproduções HTTP em produção, inspeção de código, logs locais de
Juiz pseudonimizados e testes com dependências simuladas. Uma recusa de validação
não é tratada como indisponibilidade de SQL nem como problema do renderer.

## 1. Cobertura

**Produção:** a pergunta exata retornou HTTP 200, somente texto e sem turno aprovado
no histórico. Passou por consultar_indicadores_cobertura e Juiz. Request de
correlação: `3e1f4a1b-a9c5-4d72-9557-1a5126e6939e`.

**Causa reproduzida localmente com o Juiz real:** o resumo dizia quantas NRs foram
retornadas e descrevia os critérios, mas não apresentava nenhum valor dos
indicadores. O Juiz pediu revisão por omissão dos indicadores, apesar do plano
visual. Os dados consultados eram consistentes: 137 pessoas com exigências,
272 pares pessoa/NR, 75 vigentes, 197 não vigentes sem encaminhamento, zero em
análise/agendados; 75/272 = 27,57%. Os agregados por NR conferiram com os cartões.
Os valores são um retrato autorizado, não constantes nem dados alterados.

**Correção:** resumo quantitativo curto, calculado pelo backend, distingue pessoas
de pares, informa etapas exclusivas e percentual com numerador/denominador.
Cartões, tabela, gráfico e contexto permanecem nos mesmos contratos e ordem.
Uma validação adicional impede aprovar contagens de etapas/população
inconsistentes; erro não vira conjunto vazio. Nova execução local com modelo real
foi aprovada, entregou os quatro blocos e preservou igualdade POST/histórico.
Não foi acrescentada uma revisão para tentar obter aprovação.

## 2. Listagem e seleção de evento

**Produção:** a pergunta com `evento_id` retornou HTTP 200 e recusa de validação,
sem listagem aprovada. Correlação: `d0a91c15-101b-4712-824c-65322b5622cf`.

**Causas reproduzidas:** essa formulação não entrava na intenção explícita já
existente e usava o filtro padrão de próximos eventos. O conjunto futuro estava
vazio; a resposta não explicava que não havia referência para selecionar. Ao
consultar todos os eventos atribuídos, a revisão apontou links localhost e depois
descrições desnecessárias ao pedido de seleção. Esses links vêm do cadastro, não
são construídos pela API.

**Correções:**

- A frase solicita todos os eventos atribuídos ao autenticado, com o mesmo escopo
  de autorização; não implica consultar eventos de outras pessoas.
- Seleção mostra somente título e evento_id, sem descrições, links e detalhes não
  solicitados. Evento repetido em turmas distintas usa a mesma referência.
- Conjunto vazio informa que não encontrou eventos, sem expor `evento_id` no texto. Eventos encontrados usam “referência do evento”; o identificador interno continua preservado e não é inventado.
- Em listagens detalhadas, links loopback/localhost/endereços indefinidos são
  omitidos com aviso. Os valores registrados não são reescritos nem alterados.
- Referências autorizadas são explicitamente distintas de UID, tokens e chaves
  privadas no contexto de revisão. Não concedem acesso; seleção chama novamente
  a API Java, inclusive quando o evento mudou de situação.
- Resumo **geral de evento** usa consultar_detalhes_evento e uma narrativa factual
  baseada nos campos retornados, explicitando a situação cadastrada, inclusive
  cancelamento. Resultados de treinamento continuam usando a tool de participações,
  com contagens explícitas e a ressalva de que não provam presença. Não se fazem
  dois GETs para misturar esses assuntos quando o usuário pediu só o resumo geral.
- Respostas textuais de listagens aprovadas também persistem os blocos emitidos,
  corrigindo a diferença entre POST e histórico. Histórico antigo não é migrado.

A listagem real local e o resumo geral selecionado foram aprovados pelo Juiz real e preservaram o mesmo contrato no histórico. A seleção
numérica foi consultada com o token temporário do próprio usuário; homônimos pedem
esclarecimento. Testes simulados cobrem sucesso e negação, sem fabricar eventos.

## 3. PDF

**O erro “assistente indisponível” não foi reproduzido nesta tentativa.** Após uma
consulta de distribuição por cargo em produção, o pedido exato de PDF retornou
HTTP 200, arquivo v1 e os mesmos blocos no histórico. Correlação:
`a1ddf39c-0972-4bd7-8820-5a4d04f2003f`. Portanto não há evidência para atribuir a
falha relatada a credenciais, upload ou ao mobile. Um sucesso não prova a
configuração de todos os workers nem explica uma intermitência anterior.

Verificações sobre esse arquivo já gerado:

- GET parcial respondeu 206 e os primeiros cinco bytes eram `%PDF-`.
- “Cadê o PDF?” respondeu 200, mesmo id e URL ainda válida, sem novo upload.
- Outra conta autorizada recebeu 404 ao consultar a sessão.
- Nenhum token Astro foi enviado ao domínio do armazenamento; nenhuma URL assinada
  ou conteúdo pessoal foi escrito nos relatórios ou commits.

**Defeito confirmado:** o prazo do lembrete diferia na precisão. O POST inicial
usava microssegundos, enquanto MongoDB armazena datetime em milissegundos. Não era
renovação da assinatura, mas violava a igualdade contratual da expiração.
O schema agora normaliza UTC para milissegundos, truncando sem arredondar para cima.
Regressão usa encode/decode BSON real, não só um dicionário em memória, e verifica
POST, histórico, lembrete, id e prazo iguais.

**Robustez adicional:** o pedido contextual exato recupera os filtros da consulta
de distribuição por cargo da própria sessão, revalida o schema e consulta novamente
os agregados autorizados. Não usa o texto da conversa como fonte nem as linhas da
tabela. Sem consulta compatível, pede que ela seja realizada antes do PDF. Continua
passando por guardrail/Juiz e geração existente. Sem novas chamadas de IA para
rotear esse caso; a consulta extra é necessária para revalidar dados e acesso.
Logs seguros distinguem configuração ausente e exceção no pipeline, sem payloads.

Falha de renderização/upload, URL indisponível, expiração e acesso de outra conta
continuam cobertos por mocks; nenhum deles retorna cartão de sucesso. O prazo não
é renovado ao recuperar histórico. URLs privadas não entram em texto, prompts,
resumos ou logs. A causa do erro original do tablet ainda exige status/request_id
da tentativa e logs do worker correspondente; não foi ocultada com novo timeout.

## 4. Intenção pessoal

O reconhecedor de gráficos pessoais exigia “treinamentos” ou “capacitações”.
“Minhas conclusões” não era reconhecido, e a regra ampla de “evolução + conclusões”
encaminhava para consultar_evolucao_conclusoes_equipe, mesmo após uma consulta
pessoal ou com pronome explícito. Reprodução local confirmou a tool errada.

Agora a formulação pessoal é reconhecida antes do classificador de equipe, com
as mesmas restrições de período/filtros suportados. As duas perguntas do tablet
chamam consultar_evolucao_treinamentos, que usa apenas o UID autenticado e sua
série de 12 meses. Não são gerados meses no cliente. A evolução da equipe continua
com suas regras de meses ausentes. Nova execução local retornou grafico_linhas
com 12 valores zero e igualdade no histórico, com aprovação real do Juiz.

## Testes e perguntas para o tablet

Testes automatizados usam providers e serviços simulados e o contrato real pelo
endpoint, cobrindo critérios quantitativos, intenção atual, seleção, acesso negado,
precisão BSON, falhas de PDF, isolamento por UID, links locais, histórico e os
fluxos que já funcionavam. Nenhuma conta de serviço amplia permissões.
As homologações locais reais usaram leituras autorizadas e histórico em memória
com codificação BSON; não equivalem a um deploy da revisão corrigida.

Após publicação **autorizada**, confirmar /version e executar na mesma conversa:

1. “Mostre indicadores de cobertura da minha equipe.”
2. “Mostre 100 registros da matriz de conformidade da minha equipe.”
3. “Compare a conformidade entre as unidades.”
4. “Liste 100 usuários da minha equipe em uma tabela.” e depois “Mostre 500 usuários da minha equipe em uma tabela.”
5. “Gere um gráfico de barras por cargo com base nesses dados considerando todo o escopo autorizado.”
6. “Gere um PDF da distribuição da minha equipe por cargo usando os dados autorizados desta conversa.”
7. “Cadê o PDF?” — mesmo id e expiração. Reabrir o histórico e baixar apenas por ação do usuário.
8. “Mostre a evolução mensal das minhas conclusões em um gráfico de linhas.”
9. “Mostre um gráfico de linhas dos meus treinamentos concluídos.” — mesmo escopo pessoal de 12 meses.
10. “Liste meus eventos com a referência evento_id para eu selecionar um resumo.”
11. “Faça um resumo do evento N.” usando uma referência **real** da resposta, sem inventar N.

Conferir páginas locais sem rede, totais completos dos agregados, vazios/zero,
expiração e recusa de outra conta. Sem evento retornado, o resultado correto é
esclarecimento, não fabricação de dados. Se ocorrer indisponibilidade, registrar
horário, HTTP, /version e X-Request-ID, sem tokens, URLs ou dados pessoais.

## Publicação

Correção entregue em PR separada, sem merge ou deploy. Nenhum novo discriminador
é necessário no mobile/web; arquivo v1 mantém os campos e expiração ISO 8601.
Validação final: **1.052 testes passaram**, com 15 avisos de dependências; compileall, diff --check e comparação com segredos locais passaram. O consumo local medido nesta investigação foi de 39 inferências, 155.213 tokens e US$ 0.020055325. Houve rodadas adicionais para reproduzir as justificativas e corrigir fatos/apresentação; nenhuma delas fez parte de um mecanismo de forçar aprovação no produto. As quatro reproduções HTTP em produção não retornam usage/custo ao cliente e não integram essa soma. Não houve embeddings. Revisão corrigida e CI estão na descrição da PR.
Os scripts e relatórios brutos de diagnóstico não são versionados.

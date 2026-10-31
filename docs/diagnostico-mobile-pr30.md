# Diagnóstico do chatbot mobile após a PR #30

## Evidências confirmadas em 31/10/2026

As leituras usaram a identidade GESTOR_WORKSPACE autorizada. Login temporário por
custom token Firebase; senhas não foram exercitadas. Tokens, nomes, e-mails,
identificadores de sessão, caminhos de objetos e URLs assinadas não foram incluídos
nos relatórios. As três perguntas de teste criaram uma única conversa no ambiente
publicado; não houve alterações de usuários, conformidades ou operações de negócio.

- `/version`: `commit=unknown`. OpenAPI reconhece tabela v2 e origem da ficha, mas
  isso não permite determinar o SHA efetivamente executado.
- POST real de 100 usuários: HTTP 200, somente texto. O histórico correspondente
  não continha tabela. A consulta direta no mesmo escopo retornou 100 de 141.
- POSTs reais de barras por cargo e setores por perfil: somente texto, apesar de
  `consultar_indicadores_equipe` constar nos agentes executados. O histórico não
  continha esses gráficos. Consulta direta produziu quatro categorias e gráfico.
  Pendências por NR produziram seis categorias válidas; históricos existentes
  continham gráficos de pendências e vencimentos. A falha não nasceu no renderer.
- Reprodução local com modelos reais e histórico em memória: tool retornou 100,
  Juiz marcou `revisar`, resposta determinística foi liberada e a montagem suprimiu
  a tabela. A revisão não sabia que o resumo seria acompanhado por um bloco visual.
- Há outra falha reproduzível por teste: comparar candidata com texto já convertido
  para `markdown=false` elimina os blocos quando a conversão muda a string.
- Foto própria: cadastro e registro de foto existem, autorização local permite a
  leitura, cache/assinatura local resolvem URL, GET parcial do objeto respondeu 206.
  `/user/me` da Java respondeu 200 com foto; ficha do histórico publicado tinha URL
  nula. HEAD não valida uma URL assinada para GET (pode responder 403 por método).
- Nome completo e e-mail da pessoa listada funcionaram. Primeiro nome retornou
  sem dados porque a resolução anterior exigia igualdade exata, apesar de a lista
  aceitar busca por trecho. Os escopos funcionais por perfil foram preservados.

## Correções

O Juiz recebe `resultado.apresentacao`, gerado pela aplicação, indicando tabela ou
gráfico planejado e contagens. A revisão considera texto e visual juntos sem enviar
100 cadastros ao modelo, sem acrescentar uma chamada de IA. Metadados são coerentes
com o retorno real da ferramenta. Uma rejeição continua impedindo blocos; não foi
removida a aprovação exigida pelo serviço. A conversão de transporte é feita sem
alterar a elegibilidade da candidata original. Os blocos continuam validados pelos
modelos Pydantic, persistidos na ordem e carregados no histórico. Histórico antigo
sem blocos permanece legível via content; não reconstruímos dados antigos como se
fossem uma nova consulta autorizada.

A seleção operacional reconhece também barras/setores sem a palavra gráfico e
`perfil` no singular. As agregações continuam sobre todo o escopo, independentes
do limite da tabela. Consultas parciais ou com mais de 12 categorias não inventam
um gráfico global.

Busca de ficha tenta primeiro nome completo/e-mail exato. Na ausência de nome
exato, busca trecho literal, escapando curingas SQL, no mesmo escopo e com limite
de dois candidatos. Um candidato é reautorizado antes da leitura; múltiplos exigem
e-mail/esclarecimento. E-mail nunca recebe busca parcial. Na confirmação real após a correção, nome completo, trecho único e e-mail retornaram ok; a ambiguidade foi coberta pelos testes simulados. Nenhum perfil ganhou
acesso a novas unidades ou empresas.

Para fotos, as opções AVATAR_* continuam prioritárias; quando ausentes/vazias,
aceitam as opções R2 comuns e REDIS_URL. Isso alinha a configuração compartilhada
com Java sem tornar obrigatória a duplicação das credenciais. Para contas R2 ou
Redis diferentes, usar AVATAR_* explícitos. Cache continua `user:avatar:<id>` após
autorização; host/objeto/assinatura/expiração são validados. Não são registrados
URLs, credenciais ou dados da foto. Faltas de configuração/foto autorizada/assinador
produzem motivos operacionais genéricos, mantendo URL nula e fallback de iniciais.

**Limite confirmado do diagnóstico da foto:** não houve acesso à configuração
privada do processo publicado. Assim, a variável específica que explica o null em
produção não foi confirmada; os aliases corrigem uma incompatibilidade de nomes,
e os motivos operacionais permitirão distinguir configuração, autorização e fonte.
Não copiamos automaticamente segredos locais para produção.

## Revisão de imagem e publicação

Docker recebe `APP_COMMIT_SHA` por build arg, incorpora a variável no runtime e o
label OCI `org.opencontainers.image.revision`. A publicação GHCR passa github.sha
e valida `/version` dentro da imagem antes do push. O endpoint aceita essa revisão
validada e mantém fallback para RENDER_GIT_COMMIT; valores inválidos retornam
unknown, com Cache-Control no-store. Builds manuais devem fornecer
`--build-arg APP_COMMIT_SHA=<sha-do-codigo>`.

Não houve merge ou deploy nesta investigação. O SHA do processo público continua
não verificável até reconstruir/promover uma imagem corrigida e comparar `/version`
com o SHA esperado. Uma tag desejada no GitOps ou um contrato OpenAPI compatível
não comprova qual imagem está atendendo as requisições.

## Validação e próximos passos

827 testes simulados passaram (15 avisos de dependências): POST/histórico de gráficos
em Markdown e texto simples, revisão com plano visual, tabela v2 de 100 linhas,
rejeição pelo Juiz, busca parcial/ambígua e curingas, configuração de foto e revisão
de imagem. Compileall e diff --check também passaram.

Foram feitas três perguntas reais no endpoint publicado. O endpoint não expõe
usage, portanto seu consumo não é atribuível por chamada neste relatório. Medições
locais somaram 10 chamadas, 33.581 tokens e US$ 0,00433750, incluindo uma reprodução
completa antes/depois e duas revisões isoladas. Não representam uso total da chave
compartilhada. Após a correção, a reprodução local com ferramentas/modelos reais
teve Juiz aprovado e retornou texto, tabela de 100 linhas e texto. Testes reais locais usam histórico somente em
memória e não modificam coleções vetoriais ou Mongo de produção.

O mobile já oferece tabela v2 e paginação local de 10 linhas. Não precisa alterar
esse contrato para corrigir as omissões confirmadas. Após publicar a API, repetir
POST e histórico, verificar 100 linhas/10 páginas locais, barras por cargo, setores
por perfil, foto própria e desambiguação por nome. Fallback com iniciais permanece
correto quando a foto está nula ou a URL falha. Continuam necessários os ajustes
de cliente descritos na documentação da PR #30 caso algum outro cliente ainda
não os tenha incorporado.

Operador da implantação deve conferir a presença das variáveis de avatar/comuns,
a referência ao Redis compartilhado e permissão de leitura no bucket, sem exibir
valores. Se a ficha continuar nula, consultar os motivos `avatar_unavailable` e
comparar novamente com Java. Promoção de produção depende de autorização explícita.

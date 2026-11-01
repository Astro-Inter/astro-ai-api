# Correção de persistência e continuidade dos gráficos da PR #33

## Revisão e evidências

Em 01/11/2026, `GET https://ia.astro-app.solutions/version` confirmou
`3ad06be7bf696bac04d954439e63fd8be5d66c79`, merge da PR #33. O contrato consultado
foi [dados analíticos](contrato-dados-analiticos.md). A correção é posterior a esse
SHA; o endpoint não foi atualizado por este trabalho. Sem merge ou deploy.

Reprodução HTTP com a identidade de gestor workspace já autorizada:

| Pedido | POST publicado | Histórico |
| --- | --- | --- |
| Indicadores de cobertura da minha equipe | 500, sem blocos utilizáveis | Sem turno retornado para comparar |
| 100 registros da matriz de conformidade | 500, sem blocos utilizáveis | Sem turno retornado para comparar |
| 100 usuários em tabela | 200, texto/tabela/texto | Mesmos blocos |
| Barras por cargo com base nesses dados, todo o escopo autorizado | 200, apenas texto | Nenhum gráfico persistido observado |

Os dois 500 não forneceram X-Request-ID na resposta. A listagem e sua continuação
forneceram correlação HTTP; valores e identificadores privados não são necessários
para reproduzir a regressão e não estão versionados. A continuação informou os
agentes guardrail_entrada, roteador, juiz e guardrail_saida, **sem tool de
agregação**. Isso confirma a ausência da consulta nessa resposta, não um erro do
renderer mobile ou uma conta de gráfico feita pelo cliente.

Não foi possível consultar os logs do runtime publicado: não há conector de
logs disponível nem contexto Kubernetes remoto configurado; o único contexto
é docker-desktop e seu cluster local está indisponível. Portanto não se afirma
ter obtido stack trace da produção nem o JSON intermediário do seu modelo.
Logs locais da reprodução incluem request_failed/InvalidDocument. Somente tipos,
status, contagens e metadados controlados foram registrados nos relatórios.

## Causa da falha analítica

`contexto_dados` usa campos Pydantic `date`. A resposta HTTP serializa essas datas
para ISO, mas o serviço persistia os blocos com `model_dump()` no modo Python.
Isso encaminhava `datetime.date` ao MongoDB. BSON não aceita esse tipo e levanta
`bson.errors.InvalidDocument`. O erro ocorre após tools, Juiz, guardrail e montagem
do contrato, durante a gravação atômica do turno; não indica indisponibilidade dos
modelos nem ausência de dados autorizados.

Um teste com codificador BSON real reproduziu a exceção na revisão anterior.
Os testes anteriores só usavam um dicionário em memória, que aceitava `date`.
Agora a persistência usa `model_dump(mode="json")` nos blocos. Datas permanecem
strings ISO com o mesmo valor do wire contract, sem converter dia em timestamp
ou alterar o fuso. O schema continua validando a resposta e a leitura do histórico.
Não há mudança de discriminador, versão, ordem, limites ou formato público.

## Causa da continuação sem gráfico

A continuação dependia da escolha inferida pelo roteador antes de executar a
consulta. A resposta observada não chamou consultar_indicadores_equipe. O código
apenas reaplicava alguns filtros **depois** de o modelo selecionar uma ação;
não garantia encaminhamento de uma referência explícita à listagem. Quantidade
de tabela também não deve ser copiada como página de categorias do agregado.
Sem logs intermediários não se atribui a recusa original a uma violação específica
do JSON do modelo ou a um status de um provedor.

Agora uma frase explícita e sintaticamente delimitada, como a relatada, é resolvida
pelo backend depois do guardrail de entrada. Ela usa consulta_rh da própria sessão
para consultar a distribuição completa. Nome, cargo, perfis e situações salvos são
revalidados; filtros inválidos/ausentes pedem esclarecimento, sem removê-los para
ampliar a leitura. Quantidade e página da tabela anterior não são herdadas.
Pedidos fora dessa gramática continuam pelo roteamento existente.

O guardrail recebe orientação para não negar uma consulta legítima apenas porque
o histórico contém uma amostra. Bloqueios ainda interrompem o fluxo. Juiz e
guardrail de saída permanecem obrigatórios; rejeição não entrega gráfico aprovado.
As tools continuam exigindo o contexto autenticado e as permissões existentes.

Uma segunda diferença foi encontrada na homologação: o indicador direto alcançava
144 cadastros autorizados e a listagem 141. Uma leitura SQL confirmou três registros
com firebase_uid nulo; a listagem usa `usuario.firebase_uid <> UID`, que os exclui.
O gráfico contextual agora preserva também esse predicado, além dos filtros, sem
mudar a listagem nem a consulta direta. É uma restrição adicional dentro do SCOPE
atual, nunca substituição de autorização ou ampliação de permissões.

O marcador interno referencia_listagem_rh é atribuído pelo backend e persistido
na consulta operacional para manter o critério em eventuais continuações antigas
de categorias. A seleção do modelo não pode ativá-lo por conta própria. Não é um
campo novo dos blocos nem exige alteração de web/mobile. O critério acompanha o
aviso do resultado. Não entram linhas, identidades ou offsets visuais no agregado.

## Verificação e limites

Regressões usam modelos simulados e incluem:

- Codificação BSON real do turno completo: matriz de 100 linhas, cobertura,
  evolução com período e conjuntos vazios. Datas ISO e mesmos blocos no histórico.
- Continuação com resumo “100 de 141”, modelo de roteamento incapaz de fornecer
  agregados, seleção da tool e resultado sobre 141, preservando os filtros salvos.
- Bloqueio de entrada e rejeição do Juiz, sem publicação de gráfico.
- Filtros inválidos, referência ausente e texto adicional tentando ampliar acesso.
- SQL agrega antes de LIMIT/OFFSET, mantém workspace, unidade, papel ativo e
  fn_seleciona_unidades; aplica o predicado cadastral apenas ao contexto de RH.

Também houve validação local usando modelos e leituras reais autorizadas, com
histórico em memória e codificação BSON real antes de cada atualização. Cobertura
produziu indicadores/tabela/barras/contexto; matriz coletou 100 de 272 pares.
Os blocos foram iguais aos reabertos no histórico. Esse teste não usa um servidor
MongoDB remoto: valida o mesmo codificador BSON e o comportamento do serviço.
A homologação final da continuação retornou 100 de 141 na listagem e quatro
categorias somando 141 no gráfico, com igualdade entre POST e histórico. A suíte
completa passou com **1.012 testes e 15 avisos**; depois, nove verificações focadas
passaram, incluindo duas novas regressões sobre metadados internos nas entradas
JSON/QUERY do roteador. Compileall, diff --check e comparação com segredos locais
também foram verificados.

As duas rodadas locais com IA real totalizaram 20 chamadas de inferência medidas,
71.094 tokens e **US$ 0,009203307** de custo informado pelo OpenRouter. As quatro
reproduções do endpoint publicado não retornam usage/custo ao cliente; seu consumo
não está incluído nessa soma nem foi estimado como zero. Não houve embeddings nos
cenários de diagnóstico. Os scripts de reprodução e relatórios brutos são locais
e não foram versionados.

As quatro reproduções HTTP criaram histórico de teste no ambiente publicado;
leituras SQL e homologação local não alteraram registros de negócio. Autenticação
Firebase pode atualizar seus metadados. Não houve reindexação, escrita de negócio,
mudança de credenciais ou publicação. Testes automatizados não fazem chamadas reais
de IA. A solução não acrescenta gerações: para o caso explícito, evita uma decisão
de roteamento desnecessária, mantendo as revisões e consultas necessárias.

## Publicação

A API publicada ainda precisa receber a correção após revisão e autorização de
merge/deploy. Confirmar o novo SHA em /version e repetir a sequência no staging e
no ambiente publicado autorizado. Web/mobile não precisam alterar o contrato para
esta correção; permanecem responsáveis pela renderização e paginação local já
documentadas na PR #33. O HTTP 500 original não deve ser compensado no cliente
inventando indicadores, removendo validações ou recalculando o gráfico pela tabela.


## Continuação da investigação

O anexo de homologação web trouxe casos adicionais tratados na [homologação ampliada da PR #34](homologacao-ampliada-pr34.md). A correção original mantém o contrato; a extensão solicitada de [arquivo PDF v1](arquivos-chat.md) acrescenta discriminador e exige suporte dos clientes antes de publicação. A saída determinística agora respeita vereditos negativos e indisponibilidade do Juiz; não fabrica aprovação.

# Blocos analíticos e paginação local

Contrato das dez tools de [gestão de equipe](consultas-gestao-equipe.md). Elas usam
os endpoints existentes de chat. O backend calcula métricas, autoriza cada leitura,
executa as tools e monta os blocos após aprovação do Juiz e guardrail. O modelo não
calcula percentuais, inventa categorias ou concede permissões.

## Ordem e compatibilidade

Ordem: `indicadores` → `matriz_conformidade` ou `tabela` v2 → gráfico opcional →
`contexto_dados`. Texto pode aparecer nos demais fluxos; renderizar na ordem do array.
POST e histórico mantêm os mesmos blocos e versões. Histórico antigo conserva
tabelas v1, fichas e gráficos existentes; não é recalculado com dados atuais.
Datas de referência/período são persistidas como strings ISO, compatíveis com
BSON. Diagnóstico e regressões da correção: [incidente da PR #33](correcao-producao-pr33.md).

Esses três novos tipos exigem suporte de web e mobile. A PR #33 foi incorporada
e a revisão 3ad06be estava publicada em 01/11/2026; a correção de persistência
documentada acima ainda depende de publicação autorizada. O cliente deve aceitar
novos blocos sem descartar toda a mensagem e preservar fallback para
tipos desconhecidos, sem mostrar JSON bruto ou afirmar que recebeu todos os dados.

## Quantidade versus páginas

Nas dez tools, `limite` é quantidade solicitada: padrão 100, entrada até 1.000.000,
coleta máxima **200 linhas**. `pagina` só aceita 1 por compatibilidade interna;
não é controle de navegação. SQL coleta lotes internos de até 50 no mesmo snapshot
somente leitura REPEATABLE READ, com ordenação estável e revalidação de identidade.
Falha intermediária gera indisponibilidade, sem sucesso parcial enganoso.

Há orçamento conservador de 1.500.000 bytes para linhas (inclui reserva e overhead),
texto por célula até 500 caracteres e até 10 colunas. O envelope analítico validado
tem limite de **2 MiB** em JSON com escape ASCII. Corte por quantidade ou tamanho
é explícito; a contagem completa continua disponível. `total` conta linhas da
consulta: pares pessoa/NR, meses, situações, envios ou unidades, conforme
`contexto_dados.conjunto.unidade_total`; não é sempre pessoas únicas.

- `retornada`: tamanho do conjunto enviado ao cliente.
- `solicitada`: quantidade pedida, antes dos limites de segurança.
- `tem_mais = retornada < total`: existem outros registros autorizados.
- `limitado = retornada < min(solicitada, total)`: segurança impediu atender ao pedido.
- `motivo_limite`: `maximo_resultados`, `tamanho_resposta` ou null.
- `paginacao: "local"`, `pagina: 1`, `total_paginas: 1`: envelope único, não número
  de páginas visuais. O frontend divide `linhas` em páginas de 10/20, sem rede.

Exemplos: pedir 100 de 273 retorna 100, tem_mais true e limitado false; pedir 500
retorna até 200, tem_mais true e limitado true. A interface deve mostrar ambos os
conceitos. Para ampliar o conjunto, fazer um novo pedido explícito; não solicitar
“próxima página” ao chatbot. Essa continuação não dispara outra coleta.

Listagens simples de RH já usam tabela v2 local. As demais tools antigas de
agenda, mensagens, notificações e acesso mantêm seus contratos próprios de
paginação no servidor; esta alteração não remove sua paginação indiscriminadamente.

## Indicadores v1

```json
{
  "tipo": "indicadores",
  "versao": 1,
  "titulo": "Resumo dos dados",
  "abrangencia": "conjunto_autorizado",
  "universo": "Exigências pessoa/NR do cadastro ativo autorizado antes do filtro de encaminhamento da lista.",
  "indicadores": [
    {"chave":"pares_aplicaveis","rotulo":"Exigências pessoa/NR","valor":120,"unidade":"pares_pessoa_nr","numerador":null,"denominador":null,"criterio":"Exigências do cargo e unidade no escopo autorizado."},
    {"chave":"percentual_vigentes","rotulo":"Vigência cadastral","valor":75.0,"unidade":"%","numerador":90,"denominador":120,"criterio":"Pares vigentes / pares exigidos. Não certifica aptidão legal."}
  ]
}
```

De 1 a 10 indicadores, chave única, rótulo até 120, critério até 300 caracteres.
Valor de contagem é inteiro não negativo. Unidades: `pessoas`, `pares_pessoa_nr`,
`participações`, `conclusões`, `registros`, `conflitos`, `envios`, `meses`, `%`.
Percentual exige numerador ≤ denominador e valor `round(100*n/d, 2)`; denominador
zero produz valor null, exibido como “sem base”, nunca 0% ou 100%. Contagens não
trazem numerador/denominador. Não somar pessoas únicas entre situações.

Cartões são globais pelos filtros autorizados, independentes das linhas coletadas.
Na cobertura, universo dos cartões inclui todas as exigências filtradas, **antes**
do filtro de encaminhamento aplicado à lista. Entre pares não vigentes: análise
tem prioridade, depois agendamento sem análise, depois sem encaminhamento; as
categorias são exclusivas. Uma pessoa pode ter vários pares e encaminhamentos.

Resumo de evento: participações, pessoas únicas globais, participações concluídas,
em análise e percentual concluído sobre participações. Isso não prova presença.
Evolução: conclusões registradas por mês, não histórico de conformidade percentual.

## Matriz de conformidade v1

Formato longo permite paginação local e mais de dez NRs sem criar colunas dinâmicas:

```json
{
  "tipo":"matriz_conformidade","versao":1,"titulo":"Matriz de conformidade da equipe",
  "chave_pessoa":"referencia_pessoa","chave_categoria":"nr","chave_situacao":"situacao",
  "colunas":[{"chave":"referencia_pessoa","titulo":"Referência da pessoa"},{"chave":"nome","titulo":"Nome"},{"chave":"nr","titulo":"NR"},{"chave":"situacao","titulo":"Situação"}],
  "linhas":[{"referencia_pessoa":42,"nome":"Pessoa de exemplo","nr":10,"situacao":"VIGENTE"}],
  "pagina":1,"total_paginas":1,"total":1,"solicitada":100,"retornada":1,
  "limitado":false,"tem_mais":false,"motivo_limite":null,"paginacao":"local"
}
```

Exemplo reduzido válido; saída atual também contém unidade, conformidade_id,
validade, agendado e em_analise, com correspondência exata entre colunas e linhas.
Identidade de célula é `(referencia_pessoa, nr)`, nunca nome; homônimos permanecem
distintos. Referência não concede autorização para nenhuma consulta.
Situações: VIGENTE, VENCIDA, SEM_VALIDADE, SEM_REGISTRO. Backend rejeita duplicações.
Frontend pode exibir tabela longa ou pivotar **somente dados recebidos**; célula
ausente é desconhecida, podendo estar fora do lote ou ser não aplicável. Não a
converter em vigente, pendente ou não aplicável sem evidência explícita.

## Contexto de dados v1

```json
{
  "tipo":"contexto_dados","versao":1,
  "fonte":"Consulta autorizada ao cadastro Astro","referencia":"2026-10-31",
  "inicio":null,"fim":null,"escopo":"Equipe nas unidades autorizadas do solicitante",
  "filtros":{"nr":10,"status":["ATIVO"]},
  "criterios":["Exigências do cadastro ativo autorizado; não certifica conformidade legal."],
  "avisos":["Célula ausente não comprova vigência."],
  "conjunto":{"solicitada":100,"retornada":100,"total":273,"limitado":false,"tem_mais":true,"motivo_limite":null,"unidade_total":"pares pessoa/NR"}
}
```

Exibir fonte, referência, período, filtros e avisos em rodapé/painel de contexto,
sem repetir o mesmo título na introdução, tabela e gráfico. Datas ISO, opcionais;
período sempre início/fim juntos. SQL usa CURRENT_DATE do banco no mesmo snapshot;
evento Java usa data UTC da consulta. Não inventar horário de atualização da fonte.
Referência indica o retrato consultado, não data de certificação.

Filtros permitidos: nome, cargo, unidade, nr, evento_id, conformidade_id,
incluir_agendados, status. Identidade/token/URL assinada não entram nesse mapa.
Até 12 filtros, valor serializado até 300 caracteres; até 8 critérios e 8 avisos
de até 500 caracteres. Os filtros exibidos não substituem autorização no backend.

## Tabelas, gráficos e exemplos de perguntas

Demais consultas usam `tabela` v2 com os campos locais acima, sem `chave_pessoa`,
`chave_categoria` e `chave_situacao`. Células seguem o contrato existente
escalar/null ou `{"tipo":"imagem","url":null,"descricao":"Pessoa"}`; estas
consultas não resolvem fotos nem acrescentam chamadas por pessoa.

- “Mostre indicadores de cobertura da equipe”: cartões + tabela por NR + barras
  de pares sem encaminhamento por NR, com toda a população autorizada.
- “Compare a conformidade entre unidades”: tabela com pessoas, pares aplicáveis,
  vigentes e percentual por unidade; barras com **quantidade de pares vigentes**.
  O percentual sempre acompanha numerador/denominador na tabela. Unidade com
  pessoas sem exigências tem denominador zero e percentual null. Unidades sem
  pessoas autorizadas não entram. Gestor continua limitado à própria unidade.
- “Faça um resumo desse treinamento”: cartões + distribuição por situação, com
  barras ou setores. Precisa referência de evento autorizada; caso contrário,
  pede esclarecimento.
- “Mostre a evolução mensal das conclusões”: série de linhas por padrão, barras
  opcionais. Meses sem registros são omitidos com aviso, sem inventar zeros.
- “Mostre 100 registros da matriz de conformidade”: até 100 pares, cartões globais,
  matriz e contexto, sem paginação do chatbot.

Gráficos reutilizam contratos existentes barras/setores/linhas v1, com fonte,
unidade e até 12 categorias, inclusive valores zero. Mais de 12 categorias omite
o gráfico com aviso, sem truncá-lo silenciosamente. Consulta agregada independente
da tabela garante que pedir uma linha não reduza a população do gráfico. Percentual
por unidade não é colocado no campo inteiro de valor do gráfico. Não somar rótulos
de pessoas únicas ou comparar percentuais sem seus denominadores.

Conjunto vazio: cartões de contagem zero, percentuais null, contexto total/retornada
zero, sem tabela vazia nem gráfico fabricado. Corte por tamanho pode produzir zero
linhas com total positivo: contexto diferencia isso de conjunto vazio. Rejeição do
Juiz ou guardrail não publica blocos analíticos aprovados implicitamente.

## Custo e publicação coordenada

SQL executa contagem, métricas e lotes limitados, além de agregação de gráfico quando
cabível; há mais leituras que na página antiga. Mesmo snapshot/conexão, timeout de
statement existente e limite de resposta permanecem. Evento usa um GET Java fixo,
sem fan-out, sem escrita. Não há novas chamadas de IA por cartão/linha/gráfico;
Juiz recebe agregados e plano visual, sem as centenas de linhas brutas. O catálogo
maior aumenta tokens de roteamento; validar latência em homologação.

Antes do merge/deploy: ambos os clientes devem renderizar indicadores, matriz e
contexto, paginar tabelas localmente e testar recuperação do histórico. Testar os
novos contratos com fixture, inclusive vazio, zero, corte de 200 e de bytes. Depois
homologar com API em staging e coordenar releases. Novas extensões devem aguardar
essa compatibilidade antes da publicação; nenhum dado antigo precisa de migração.

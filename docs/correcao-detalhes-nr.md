# Detalhes de NR após consulta de pendências

## Diagnóstico confirmado

Na homologação de 02/11/2026, “Quero detalhes da NR1”, após pendências pessoais,
recebeu recusa de validação. A revisão publicada consultada era
`a96be9fc8a0896303acf28cc8449e5a183c8e5d5`, já com a PR #42 incorporada.

O trace das 14:00:03 UTC (11h em São Paulo) confirmou a execução de
`consultar_nrs`, número 1, modo detalhar, com origem principal `web_oficial`.
A ferramenta retornou nome, situação, resumo e conteúdo complementar interno.
O Juiz pediu revisão porque não recebeu os valores usados na candidata:
a projeção `_evidencia_compacta_nrs` continha números e nomes de campos,
mas omitia os registros. A candidata, corretamente, não foi aceita como prova
de si mesma. Não foi falha de renderização do mobile nem bloqueio do Jev.

Traces brutos permanecem apenas no diagnóstico local, fora do Git. Não se
reproduzem dados pessoais, credenciais ou identificadores de sessões aqui.

## Correção

A evidência inclui agora `nrs`, com os valores que sustentam a apresentação:

- Listagem: número, nome e situação.
- Detalhamento: esses campos, resumo oficial, objetivo, descrição, aplicabilidade,
  revogação, reciclagem, usabilidade e referência oficial, quando retornados.

Fontes, origem principal, contagens e filtros existentes são preservados.
Metadados de criação/atualização e campos não apresentados não são copiados
para os registros de evidência. Ausência e `null` continuam ausências;
não se completa conteúdo com conhecimento do modelo ou histórico.

A consulta pública não comprova conclusão, validade ou conformidade pessoal.
Detalhes de uma atividade atribuída continuam exigindo a ferramenta autorizada
correspondente. A identidade e as verificações de acesso não foram alteradas.

O Juiz e o guardrail permanecem ativos. Não se converte recusa em aprovação,
não se modifica o contrato público de blocos e não se reescreve o histórico.
POST e histórico mantêm a mesma resposta aprovada. Web e mobile não precisam
de um novo componente para este ajuste.

## Custo e validação

Não há nova chamada de IA no fluxo: a revisão existente passa a receber os
fatos que antes eram omitidos. Isso aumenta tokens de entrada proporcionalmente
ao conteúdo efetivamente apresentado. As listagens conservam evidência curta;
os limites existentes da ferramenta para detalhamento são preservados.

Uma chamada real ao Juiz com dados inteiramente sintéticos aprovou a resposta:
3.304 tokens, custo observado de US$ 0,0004268. Não houve consulta privada,
alteração de cadastro ou deploy nessa verificação.

As regressões cobrem valores reais na evidência, seleção dos campos, preservação
do resultado original, sequência de pendências seguida de detalhes, fonte ausente,
consulta indisponível, recusa do Juiz e igualdade POST/histórico.

Validação automatizada: **315 testes aprovados**, incluindo seis regressões novas,
nos arquivos `test_nr_detail_evidence.py`, `test_chat.py` e `test_sst_tools.py`.
Os dois avisos são de dependências do ambiente Python local. Modelos e ferramentas
externas são simulados nesses testes; a única chamada real é a verificação
sintética descrita acima.

Após publicação autorizada, repetir no tablet:

1. “Tenho pendências de NR?”
2. “Quero detalhes da NR1” na mesma conversa.
3. Conferir fonte, explicação e histórico, sem interpretar vigência da norma como
   confirmação de que a pessoa está em dia.

Nenhum merge ou deploy é realizado por esta correção.

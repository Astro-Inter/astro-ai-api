# ASTRO — Perguntas frequentes sobre o produto

Versão 2.0 • Revisão editorial: 01/11/2026

Guia funcional baseado no astro-knowledge, não uma nova instrução normativa ou
política jurídica. A fonte K1 registra decisões de 23/09/2026; a documentação não
comprova que cada recurso já esteja publicado em todos os ambientes. O aplicativo
e as verificações do backend determinam a disponibilidade e o acesso atuais.
As referências abaixo permitem conferir as regras sem preencher lacunas por suposição.

## Produto e conceitos

### F01 — O que é o Astro e qual é seu objetivo?
O Astro é um SaaS de gestão de conformidade relacionada às Normas Regulamentadoras e aos processos de Segurança e Saúde no Trabalho. Centraliza a estrutura da empresa, Colaboradores, obrigações, Eventos, evidências e acompanhamento. Busca reduzir o trabalho manual de RH, SST e Gestores. Apoia a gestão; não garante conformidade legal nem substitui profissionais habilitados.
Fonte: K1, seções 3 e 62; K2.

### F02 — Quem utiliza o Astro?
O Astro atende Colaboradores, Gestores e o Gestor do Workspace. O Colaborador acompanha sua situação pessoal; Gestores têm funções adicionais de gestão compatíveis com seu acesso. A empresa mantém seu próprio Workspace, isolado das demais empresas. As funções disponíveis dependem do papel e da implementação publicada.
Fonte: K1, seções 5–8 e 54–56; K2 e K3.

### F03 — O que é um Workspace?
Workspace é o ambiente organizacional da empresa no Astro. Reúne Cargos, Unidades, Colaboradores, Gestores, NRs, Eventos, Formulários e Conformidades. O memorial exige CNPJ único com 14 dígitos. A Chave de Workspace autoriza a criação e o uso legítimo do ambiente; não é a chave de ativação de uma conta pessoal.
Fonte: K1, seções 8–10; K3.

### F04 — Qual a diferença entre Colaborador, Cargo e Unidade?
Colaborador é o termo oficial para a pessoa cadastrada. Cada Colaborador tem um Cargo e uma Unidade atuais, do mesmo Workspace. O Cargo define as NRs individuais obrigatórias; a Unidade organiza o contexto operacional, como inspeções e Formulários. O termo antigo Funcionário pode aparecer em perguntas, mas a documentação usa Colaborador.
Fonte: K1, seções 4, 5, 11 e 12; K2 e K5.

### F05 — O Astro substitui auditoria, parecer ou responsabilidade profissional?
Não. O Astro organiza informações e apoia decisões. Sugestões de IA e cálculos auxiliares não são parecer jurídico e não garantem conformidade legal. A empresa responde pelos dados informados; profissionais habilitados continuam responsáveis pelas decisões técnicas e legais.
Fonte: K1, seção 62; K9.

## Papéis e acesso

### F06 — Quais são as diferenças entre os papéis de acesso?
O Colaborador consulta suas informações e realiza ações pessoais permitidas. O Gestor mantém essa mesma conta e recebe funções adicionais, como administrar Eventos e consultar informações autorizadas da equipe. O Gestor do Workspace possui as funções de Gestor e administra a principalidade e a promoção de Gestores. O papel não permite ultrapassar o escopo autorizado pelo backend.
Fonte: K1, seções 6, 7 e 50; K3.

### F07 — Um Gestor precisa de duas contas?
Não. Gestor continua sendo Colaborador, com a mesma conta, Cargo e Unidade. A promoção acrescenta permissões; não cria outra identidade. A conta permite alternar entre as funções pessoais e as funções de gestão disponíveis para o papel.
Fonte: K1, seções 7 e 50; K3.

### F08 — Quantos Gestores do Workspace podem existir?
O memorial define exatamente um Gestor do Workspace por Workspace. Essa pessoa pode promover ou adicionar Gestores e transferir a principalidade para alguém que já seja Gestor. Transferir a principalidade não autoriza outro Gestor a validar automaticamente Eventos criados por uma pessoa diferente.
Fonte: K1, seções 6.3, 33, 50 e 51; K3 e K6.

### F09 — Como transferir a responsabilidade principal pelo Workspace?
No fluxo normal, o Gestor do Workspace atual seleciona um Gestor existente e confirma a transferência. O novo responsável assume a principalidade; o anterior a deixa e, se permanecer na empresa, pode continuar como Gestor comum. O fluxo preserva um único responsável principal.
Fonte: K1, seção 51; K3.

### F10 — E se o Gestor do Workspace perder o acesso ou sair da empresa?
O memorial prevê transferência excepcional: outro Gestor solicita análise à equipe Astro, informa motivo e novo responsável e apresenta comprovações. A transferência depende de análise e aprovação; não é automática nem pode ser autorizada pelo chatbot. O histórico deve ser preservado.
Fonte: K1, seções 52 e 53; K3.

### F11 — Chave de Workspace e chave de primeiro acesso são a mesma coisa?
Não. A Chave de Workspace autoriza o ambiente empresarial. A chave de primeiro acesso ativa a conta de um Colaborador recém-cadastrado. Nenhuma dessas chaves deve ser enviada ao chatbot como forma de obter privilégios; as verificações de acesso ocorrem no fluxo próprio do aplicativo.
Fonte: K1, seções 10, 18 e 19; K3 e K4. Orientação de segurança: contrato público da API de IA.

## Cadastro e importações

### F12 — Como funciona o primeiro acesso?
Segundo o memorial, o Colaborador recém-cadastrado fica pré-cadastrado e recebe uma chave. Informa a chave, o sistema a valida, cria a senha e passa a ativo; a chave utilizada é invalidada. Depois da ativação, o login normal usa e-mail e senha, conforme a situação de acesso da conta.
Fonte: K1, seções 17–20 e 66; K4.

### F13 — Quanto tempo dura a chave de primeiro acesso e como reenviar?
O memorial estabelece validade de 7 dias para a chave de primeiro acesso. Um Gestor pode solicitar reenvio: uma nova chave é enviada, a anterior é invalidada e começa outro prazo de 7 dias. O uso bem-sucedido também invalida a chave. Essa regra trata da ativação pessoal, não da Chave de Workspace.
Fonte: K1, seção 19; K4.

### F14 — Quais dados e colunas são usados na importação de Colaboradores?
O formato funcional é NOME | EMAIL | CPF | CARGO | UNIDADE | MODALIDADE. O CPF identifica o Colaborador na importação; e-mail é dado cadastral e serve ao login normal. As modalidades descritas são presencial, remoto e híbrido. Conflitos cadastrais adicionais não detalhados precisam de decisão, sem associação arbitrária de contas.
Fonte: K1, seções 16, 20 e 21; K4 e K10.

### F15 — A planilha cria Cargos e Unidades automaticamente?
Na criação inicial do Workspace, o memorial permite identificar os valores distintos da planilha e criar Cargos e Unidades antes de associar Colaboradores. Em importações posteriores, valores desconhecidos não devem ser criados silenciosamente: o Gestor resolve as correspondências na interface.
Fonte: K1, seções 9.1 e 21.4; K3 e K4.

### F16 — O que a importação atualiza em um Colaborador existente?
Quando o CPF já existe, a importação pode sincronizar nome, e-mail, Cargo, Unidade e modalidade. Não altera automaticamente papel ou permissão, senha e status de acesso. Mudança de Cargo recalcula as NRs aplicáveis e as Conformidades, preservando o histórico anterior.
Fonte: K1, seções 21.3, 22 e 67; K4.

### F17 — O que acontece com as Conformidades quando o Cargo muda?
NR que deixa de ser exigida torna a Conformidade não aplicável; NR que continua exigida preserva estado e histórico; NR que passa a ser exigida cria ou reativa a Conformidade correspondente. A troca de Cargo não deve apagar o histórico útil.
Fonte: K1, seções 22 e 72; K4 e K5.

## NRs e Conformidades

### F18 — As NRs obrigatórias vêm do Cargo ou da Unidade?
Na regra funcional documentada, a obrigação individual de NR vem exclusivamente do Cargo cadastrado. NRs associadas à Unidade servem de contexto para Formulários, inspeções e operação; não criam por si só obrigações individuais. A situação real da pessoa deve ser consultada no cadastro autorizado, não inferida de um cargo declarado no chat.
Fonte: K1, seções 11–15; K5. Consulta individual: contrato da API de IA.

### F19 — O que é uma Conformidade no Astro?
Conformidade representa o estado atual útil de um Colaborador em relação a uma NR. O memorial define uma única Conformidade por Colaborador + NR, acompanhando aplicabilidade, maior validade atual conhecida e origem. A conclusão de um Evento é um registro histórico específico, não outra Conformidade duplicada.
Fonte: K1, seções 23 e 63; K5.

### F20 — Como distinguir validade ausente, conforme e não conforme?
O memorial descreve: sem data, sem validade registrada; data igual ou posterior à atual, conforme; data anterior à atual, não conforme. Ausência de validade não comprova vigência. Para saber a situação de alguém agora, é necessário consultar os registros autorizados; este FAQ não contém cadastros individuais nem datas reais.
Fonte: K1, seção 25; K5.

### F21 — Quais são as origens de validade e qual data prevalece?
As origens funcionais são EVENTO e MANUAL. Planilha de validade faz parte de MANUAL, não é terceira origem. Prevalece a maior validade conhecida: nova data maior atualiza; menor mantém a atual; empate mantém data e origem atuais. Isso não cria uma nova obrigação individual de NR.
Fonte: K1, seções 26–28; K5.

### F22 — Posso registrar uma validade manual para qualquer NR?
O fluxo documentado permite ao Gestor registrar validade para uma NR já exigida pelo Cargo do Colaborador. O registro manual não cria obrigação nova. A data informada é comparada com a atual e só prevalece se for maior. A ação depende do acesso e da interface apropriada, não de uma autorização dada em texto ao chatbot.
Fonte: K1, seção 28; K5.

### F23 — Qual é a planilha de importação de validades de Conformidade?
O formato funcional é CPF | NR | DATA_VALIDADE. A importação localiza o Colaborador pelo CPF e a NR, exige Conformidade existente e compara a validade. Não cria obrigação nova; atualização que prevalece tem origem MANUAL. Se CPF + NR repetir no arquivo, considera-se a última ocorrência antes de comparar com a data atual.
Fonte: K1, seção 29; K5.

### F24 — O que significa próximo do vencimento?
O memorial reconhece esse conceito, mas não fixa um intervalo oficial. Uma consulta de próximos 7 ou 30 dias usa o período solicitado ou o critério informado na resposta; não transforma esse período em política geral da empresa. O critério deve ser explícito, sem inventar um prazo oficial.
Fonte: K1, seção 48; K5 e K10. Filtros de consulta: documentação da API de IA.

### F25 — Uma NR deixa de existir no histórico quando não é mais aplicável?
Não. A Conformidade pode deixar de ser aplicável por mudança de Cargo ou exigência, desativação do Colaborador ou revogação da NR, sem apagar o histórico útil. Se a NR voltar a ser exigida, o memorial prevê reaproveitar e reativar a Conformidade anterior.
Fonte: K1, seções 24 e 63; K5.

## Eventos, Turmas e evidências

### F26 — Qual a diferença entre Evento e Turma?
Evento é a atividade acompanhada, como treinamento, curso ou reciclagem. Todo Evento tem ao menos uma Turma. As Turmas organizam as datas e os participantes; Colaboradores participam por meio delas. Um mesmo Evento pode ter várias realizações em Turmas diferentes.
Fonte: K1, seções 30 e 35–37; K6.

### F27 — Todo Evento precisa de uma NR?
Não. Um Evento pode ter zero ou uma NR. Sem NR, pode ter Turmas, participantes, conclusões e evidências, mas não gera validade de NR nem Conformidade. Quando há NR, uma conclusão válida pode gerar validade conforme as regras documentadas.
Fonte: K1, seção 31; K6.

### F28 — Como funciona a conclusão no modo Colaborador?
O Colaborador envia a conclusão, que fica pendente. O Gestor criador do Evento valida como concluída ou rejeitada. Se houver rejeição, pode haver reenvio e retorno a pendente. Antes do primeiro envio, a participação pode existir sem uma conclusão registrada. Anexar certificado não substitui a validação exigida.
Fonte: K1, seções 32.1, 38 e 40; K6.

### F29 — Como funciona o modo Gestor e quem valida o Evento?
No modo Gestor, o Gestor criador registra a conclusão diretamente como concluída. O criador é o responsável pelo Evento e o único Gestor que valida suas conclusões; não participa do mesmo Evento como Colaborador. Outro Gestor não assume a validação automaticamente, inclusive por transferência da principalidade do Workspace.
Fonte: K1, seções 32.2 e 33; K6 e K10.

### F30 — Quantas evidências podem ser anexadas e quando são obrigatórias?
O memorial permite até três evidências por conclusão. A possibilidade de anexar evidência está sempre disponível; o Evento define se é opcional ou obrigatória. Quando obrigatória, exige-se pelo menos uma. Quando opcional, a conclusão pode ser enviada sem evidência. Certificado pode ser usado como evidência.
Fonte: K1, seções 39 e 40; K6.

### F31 — Como um Evento gera a validade de uma NR?
Para Evento com NR e conclusão válida, a fórmula documentada é data de validação + tempo de reciclagem da NR, comparada com a validade já registrada; só a maior prevalece. O tempo vem da configuração da NR. O memorial não esclarece completamente qual instante representa essa data no registro direto do modo Gestor; não substitua por uma data inventada.
Fonte: K1, seção 41; K5, K6 e K10.

### F32 — Cancelar um Evento apaga seu histórico?
O cancelamento deve preservar registro, motivo, contexto e histórico. O memorial descreve estados ativo e cancelado; situações como agendado ou finalizado podem ser derivadas das Turmas e datas. Não detalha todos os campos editáveis nem todos os efeitos do cancelamento sobre conclusões anteriores; esses efeitos não devem ser presumidos.
Fonte: K1, seção 34; K6 e K10.

## Formulários, canais e perfil

### F33 — Qual é a relação entre Formulário, Unidade e NR?
Formulário serve a inspeções, checklists e verificações operacionais. No memorial, é criado por Gestor e exige Unidade. A NR é opcional; se escolhida, deve estar associada à Unidade selecionada. Responder ao Formulário não gera Conformidade individual do Colaborador.
Fonte: K1, seção 42; K7.

### F34 — É preciso localização para responder um Formulário?
O memorial descreve resposta pelo Gestor no Mobile, com localização ativa e registrada no envio. Não estabelece raio oficial, distância mínima, geofence ou tolerância em metros. Não é correto apresentar um limite de proximidade como regra aprovada sem outra fonte que o defina.
Fonte: K1, seções 42 e 44; K7 e K10.

### F35 — Qual a diferença entre Chat e chatbot?
Chat é comunicação operacional entre Gestor e Colaborador; o memorial não prevê Chat direto entre dois Colaboradores. Chatbot é o assistente de consulta, disponível para Colaboradores e Gestores, que ajuda a compreender informações autorizadas, Conformidades e pendências. Conversar não altera automaticamente os registros operacionais.
Fonte: K1, seções 45 e 46; K8.

### F36 — O que o chatbot pode fazer e o que exige outra tela?
O chatbot consulta informações autorizadas e explica situações e pendências. Não cria ou altera cadastros, Eventos, NRs ou Conformidades, não registra nem valida conclusões e não concede permissões. Essas ações usam os fluxos apropriados do produto. Explicar uma regra de administração não significa executá-la pelo chat.
Fonte: K1, seção 46; K8.

### F37 — O que muda entre Web e Mobile?
O memorial concentra no Web as tarefas administrativas dos Gestores, como estrutura do Workspace, importações e configuração de Eventos. O Mobile reúne consultas e ações pessoais dos Colaboradores e funções adicionais permitidas aos Gestores, como validação de seus Eventos e resposta a Formulários. A disponibilidade atual depende do papel e da versão publicada.
Fonte: K1, seções 54–56; K8.

### F38 — É obrigatório ter foto de perfil?
Não. A foto não é requisito funcional e sua ausência não impede usar o Astro. No chatbot, uma foto indisponível não deve ser inventada; o aplicativo pode apresentar iniciais. Dados pessoais e fotos de outras pessoas continuam sujeitos às verificações de acesso do sistema.
Fonte: K1, seção 57; K8. Fallback visual: contrato ficha_usuario da API de IA.

## IA, segurança e limites da documentação

### F39 — A IA de análise de NRs é a mesma coisa que o chatbot?
São finalidades distintas. O recurso assistivo de análise pode sugerir NRs para empresa e Cargo considerando contexto, atividades e riscos; o chatbot consulta e explica informações autorizadas. Sugestões precisam de justificativa, revisão e decisão humana. Não criam obrigações automaticamente, nem autorizam o chatbot a inferir NRs de um cargo apenas declarado na conversa.
Fonte: K1, seções 46 e 58–60; K8 e K9.

### F40 — A IA pode garantir cálculo de CIPA ou conformidade legal?
Não. O memorial prevê apoio auxiliar a cálculos dependentes de CNAE, quantidade de Colaboradores e contexto, mas não fornece fórmulas legais detalhadas. Resultados não garantem conformidade legal nem substituem análise profissional. Critérios ausentes não devem ser completados por suposição.
Fonte: K1, seções 61 e 62; K9.

### F41 — O FAQ pode dizer quem está conforme ou quantas pessoas tenho na equipe?
O FAQ explica regras e conceitos; não contém os registros atuais da empresa. Perguntas sobre pessoas, Eventos, vencimentos, totais ou indicadores exigem consulta às ferramentas autorizadas. Os dados devem respeitar usuário, empresa e filtros. Histórico ou exemplos da documentação não provam a situação atual de uma conta.
Fonte: K1, seções 5, 46 e 63; contratos de consulta e evidência da API de IA.

### F42 — Por que o assistente pode pedir esclarecimento ou recusar uma resposta?
Pode faltar um referente inequívoco, filtro, registro ou fonte suficiente para responder com segurança. O assistente deve aproveitar o contexto da sessão, mas não inventar valores, executar operações sem ferramenta nem ampliar acesso. Ausência de dados não comprova que uma NR está vigente ou que não há pendências.
Fonte: K1, seções 46, 62 e 77; regras de Juiz, guardrails e sessões da API de IA.

### F43 — O chatbot pode gerar um PDF com informações autorizadas?
A API de IA oferece geração de PDF a partir de conteúdo confirmado. O documento só existe após a ferramenta gerar e disponibilizar o arquivo; uma promessa ou texto no chat não é um PDF gerado. Falha, falta de dados ou reprovação não deve produzir cartão de sucesso. O download usa link temporário e depende de disponibilidade e autorização.
Fonte: contrato arquivo v1 e fluxo de PDF da API de IA. Não altera cadastros operacionais do produto.

### F44 — Posso tratar qualquer detalhe do memorial como recurso já disponível?
Não. O astro-knowledge registra regras funcionais, mas suas notas não confirmam implementação em todos os ambientes. Permanecem lacunas, como métricas definitivas, detalhes de Notificações, versionamento de Formulários, geofence e alguns efeitos de cancelamento. A disponibilidade atual precisa ser conferida no aplicativo e no backend.
Fonte: K1, seção 77; índice K2 e pendências K10.

### F45 — O que este FAQ informa sobre privacidade?
O acesso deve respeitar o Workspace e as permissões da conta. Este guia não define política jurídica de privacidade, retenção, operadores, SLA ou condições comerciais. A documentação funcional não permite prometer ausência de compartilhamento de dados com fornecedores. Consulte a política de privacidade aplicável e a equipe Astro para esses detalhes.
Fonte: K1, seções 5 e 62; K3. Limites da fonte: K10.

### F46 — Qual é o contato e o horário de suporte do Astro?
O suporte do Astro atende pelo e-mail app.4str0@gmail.com, de segunda a sexta-feira, das 07h às 18h. Contato e horário foram confirmados pelo responsável pelo produto em 01/11/2026. Esse horário não estabelece prazo garantido de resposta, SLA, plantão, telefone ou política de atendimento em feriados.
Fonte: confirmação do responsável pelo produto em 01/11/2026; contato e horário do FAQ anterior revalidados.

## Referências consultadas

K1 — Memorial Descritivo Funcional Final — ASTRO, 23/09/2026.
K2 — Índice funcional e visão geral do astro-knowledge.
K3 — Workspace e papéis.
K4 — Cadastro e importação.
K5 — NRs e validade.
K6 — Eventos, Turmas e evidências.
K7 — Formulários e inspeções.
K8 — Comunicação e interfaces.
K9 — Análise de NRs e cálculos.
K10 — Pendências e evolução funcional.

Repositório: https://github.com/Astro-Inter/astro-knowledge
Revisão remota conferida: f051f16014f1b85d7d9bc7d02f37e347dacfd097.
Memorial: docs/astro/fontes/memorial-descritivo-funcional-2026-09-23.md.
Notas: docs/astro/README.md e documentos vinculados pelo índice.

Contratos complementares: astro-ai-api, revisão 0d50610 (main consultada),
com arquivo v1, ficha_usuario e consultas autorizadas documentadas no repositório.
A revisão editorial não afirma que alterações ainda em PR já foram publicadas.

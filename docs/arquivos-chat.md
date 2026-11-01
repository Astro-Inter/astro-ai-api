# Arquivo PDF como bloco do chatbot

O download de PDF gerado passa a ser um bloco `arquivo` v1. O texto continua no
bloco `texto`; o link não é mais embutido em Markdown ou texto simples. A geração
continua explícita, após consulta autorizada, Juiz e guardrail. Nenhuma geração
extra de IA é adicionada para montar o cartão.

## Contrato exato

```json
{
  "tipo": "arquivo",
  "versao": 1,
  "id": "fc9acaf4-4ea1-416b-a1ed-dc2d1c3d90b4",
  "nome": "relatorio-de-rh.pdf",
  "mime_type": "application/pdf",
  "tamanho_bytes": 76360,
  "url": "https://files.example/relatorio.pdf?assinatura=EXEMPLO",
  "expira_em": "2026-11-01T20:00:00Z",
  "estado": "disponivel"
}
```

O exemplo usa URL fictícia. Campos:

| Campo | Regra |
| --- | --- |
| tipo / versao | `arquivo` / 1 |
| id | UUID opaco do arquivo dentro desta conversa; não é permissão nem endpoint |
| nome | Nome seguro terminado em .pdf, até 100 caracteres; sem diretório |
| mime_type | Somente application/pdf nesta versão |
| tamanho_bytes | Inteiro positivo até 2.000.000; null quando metadado legado não está disponível |
| url | HTTPS temporário ou null; nunca construir URL no cliente |
| expira_em | Data/hora ISO 8601 UTC; não renovar prazo ao abrir histórico |
| estado | disponivel, expirado ou indisponivel |

O arquivo é anexado ao final dos blocos da resposta, mantendo a ordem dos demais.
Expiração usa precisão de milissegundos, compatível com BSON, para que POST,
histórico e lembretes exponham exatamente o mesmo prazo. Valores históricos com
microssegundos são normalizados na leitura, sem renovar a assinatura.
Pode coexistir com texto, tabelas, ficha e gráficos. Não serve para fotos de usuário.
O limite de upload atual é 2.000.000 bytes e a validade do link é cinco horas.
Essa validade não é promessa de retenção física eterna do objeto.

## Web e mobile

Adicionar `arquivo` v1 ao parser e um cartão com ícone PDF, nome, tamanho quando
disponível e botão “Baixar PDF”. Abrir a URL somente por ação do usuário; não
enviar o token da API Astro a outro domínio. Não fazer preview/fetch automático
para validar o link: isso expõe capacidades e pode baixar um arquivo desnecessário.
Botão habilitado apenas com estado disponivel, URL presente e prazo ainda válido.
O relógio do cliente ajuda a desabilitar o botão, mas não substitui a autorização
do servidor e do armazenamento. Tratar erro de download sem inventar outro link.
Para downloads via fetch no web, o bucket também precisa permitir a origem por
CORS. Consulte a [auditoria e política candidata restrita](homologacao-web-recorrencia-cors.md).
O CORS da API não é o CORS do armazenamento; ausência de headers não autoriza
renovar assinaturas ou tornar o bucket público.

Quando expirado, mostrar “Link expirado; solicite a geração novamente”. Quando
indisponível ou URL null, mostrar indisponibilidade; não oferecer link quebrado.
Para metadados de tamanho ausentes, omitir o tamanho. Não estimá-lo pelo texto.
Evitar duplicar o cartão com um link textual gerado pelo frontend.

O contrato é o mesmo em markdown=true/false e no histórico. Um pedido “Cadê o
PDF?” reutiliza o último arquivo ainda válido e seu id/prazo original, sem upload
ou renovação da assinatura. Geração/upload/revisão sem sucesso não produz bloco
de arquivo e não promete envio futuro. Mensagens antigas com links em texto não
são migradas ou regeneradas automaticamente.

## Autorização e persistência

POST e histórico verificam o dono da sessão. O backend persiste o bloco com URL
null e conserva somente a referência do arquivo na mensagem. URL e expiração
vivem no mapa privado `pdf_entregas` do documento de sessão e em ultimo_pdf para
continuidade. Após verificar ownership, o serviço preenche a URL se ainda válida.
Expiração retorna estado expirado e URL null; assinatura não é renovada. Id não
autoriza consultar arquivo de outra sessão. A URL entregue é uma capacidade
temporária: não copiar para logs, analytics, traces ou armazenamento público.

Resumos e recuperação de memória recebem somente role/content públicos das
mensagens. URLs e metadados privados não entram em prompts, embeddings ou texto
persistido da conversa. Não há credenciais de R2/OpenRouter no cliente.

## Verificação e publicação

A [homologação mobile sobre a7fbe68](homologacao-mobile-a7fbe68.md) registra download
real, autorização e a correção de precisão do prazo e do pedido contextual de PDF.

Testes simulam geração, upload, falha, expiração, lembrete, acesso de outro UID,
formato inválido, memória/resumo e igualdade POST/histórico enquanto válido. O PDF
real e sua assinatura continuam cobertos pelos testes da tool com armazenamento
simulado. Não houve upload de arquivo real nem deploy nesta extensão.

**Dependência de publicação:** web e mobile precisam suportar o novo discriminador
antes de habilitar esta revisão da API. A correção dos erros da PR #33 não exige
mudança nos contratos antigos, mas esta extensão de PDF exige atualização dos
clientes. Coordenar os releases; não fazer merge/deploy sem autorização.

O diagnóstico de falha anterior à geração e as correções de continuidade estão em [homologação de PDF e contexto](homologacao-pdf-contexto-novembro.md).

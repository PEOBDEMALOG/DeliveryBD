# Dívida Técnica — PEO-BD

## Pendências

### Senhas de usuários demo em texto puro no código
`core/auth.py` guarda usuário/senha dos 3 usuários de demonstração
(`timoteo`, `carlos`, `erick`) hardcoded em texto puro no dicionário
`USUARIOS`. Não há cadastro em banco nem hashing.

**Por quê:** decisão original do projeto para simplificar a demo (comentário
no próprio arquivo). Depois da rotação de segurança (10/07/2026), os valores
atuais não estão mais expostos em nenhum lugar do histórico anterior a essa
rotação, mas o padrão em si continua sendo credencial em texto puro
versionada no repositório — qualquer rotação futura exige o mesmo processo
manual, e o valor fica visível a qualquer um com acesso de leitura ao repo.

**O que fazer:** mover para variável de ambiente (mesmo padrão já aplicado a
`JWT_SECRET`) ou, melhor, cadastro real de usuário com senha hasheada no
banco. Não bloqueante para a apresentação de 13/07 — item de próximo ciclo.

### JWT_SECRET de produção (delivery-bd) pendente de rotação manual
O `JWT_SECRET` do ambiente de teste (`origin`/`projeto-bd`) já foi
rotacionado e configurado no Vercel em 10/07/2026 (valor novo, gerado com
`openssl rand -hex 32`, sem reaproveitar o antigo exposto no código).

**Pendente:** o ambiente de produção (`producao`/`delivery-bd`) está numa
conta Vercel à qual o agente não tem acesso. Se o valor de `JWT_SECRET`
configurado lá foi copiado das instruções originais (que incluíam o valor
antigo `peo-bd-demo-secret-2026`, hoje já removido do código), ele precisa
ser trocado manualmente por quem tem acesso a essa conta — gerar novo valor
com `openssl rand -hex 32` e não reaproveitar nenhum valor já usado em outro
ambiente. Ação do responsável do projeto, não do agente.

### Catálogo de erros (tipos_erro) desalinhado do código real
Levantamento feito em 12/07/2026 (`scripts/seed_tipos_erro.py`,
`agents/agente_resolvedor.py`, `agents/agente_classificador.py`,
`agents/agente_ingestor.py`).

**6 dos 14 tipos catalogados nunca são disparados organicamente por nenhum
caminho de código** — existem só como linha na tabela `tipos_erro`, e só
aparecem no sistema se alguém chamar `AgenteResolvedor.tratar_erro()`
manualmente (é o que `scripts/simular_erros_demo.py` faz, de propósito, pra
demo):
- `NF_DUPLICADA` — dedup real é silencioso (`_processar_linha` retorna
  `"duplicata"` sem nunca chamar o Resolvedor)
- `HASH_DUPLICADO` — mesmo caso, dedup por hash é silencioso
- `CLIENTE_NAO_RECONHECIDO` — `_resolver_cliente` cria um cliente novo
  silenciosamente em vez de reportar erro
- `REGIAO_INVALIDA` — `_agrupar_por_regiao` cai num fallback `"default"`
  silencioso, nunca reporta
- `VALOR_FORA_FAIXA` — não existe nenhuma validação de faixa de
  valor/peso em lugar nenhum do código
- `ATA_VENCIDA` — o comportamento real de ATA vencida acontece via
  **alerta `ata_prazo` do Classificador** (taxonomia completamente
  separada, ver abaixo) — este código do catálogo nunca dispara

(`TIMEOUT_SAP`, `TIMEOUT_UPS`, `FALHA_API_TRANSPORTADORA` e
`BANCO_INDISPONIVEL` **têm** gatilho real no código via
`_mapear_codigo_erro()` em `orquestrador.py` — só não são simuláveis por
upload de arquivo porque exigem falha real de rede/SMTP/banco. Diferente
categoria de problema, não incluídos nos 6 acima.)

**Acoplamento frágil identificado:** a validação de schema/CD (Correção 7,
`agente_ingestor.py`) não tem código de erro dedicado — é classificada como
`COLUNA_AUSENTE` ou `CD_INDISPONIVEL` só porque a mensagem de exceção
contém as palavras "coluna"/"cd não", que `_mapear_codigo_erro()` casa por
substring. Não é by design — se o texto da mensagem de erro mudar no
futuro (ex: numa revisão de UX das mensagens), a classificação quebra
silenciosamente sem nenhum teste acusar.

**Achado relacionado (12/07/2026):** `numero_remessa` é `VARCHAR(20)` no
banco, mas não há validação de tamanho na ingestão — um valor maior gera
`StringDataRightTruncationError` cru, propagado como `HTTP 500` sem
mensagem amigável, em vez de um erro tratado (ex: `COLUNA_AUSENTE`/erro de
validação). Não corrigido agora — fora do escopo desta tarefa.

**Recomendação:** antes de qualquer cliente real usar o sistema em
produção, ou (a) implementar de fato os gatilhos que faltam para os 6
tipos acima, ou (b) remover do catálogo os que não têm intenção real de
implementação — hoje a tabela `tipos_erro` passa a impressão de cobertura
que o código não entrega.

### Cron do Resolvedor roda só 1x/dia — limitação do plano Vercel, não bug
`vercel.json` agenda `/api/resolvedor/executar` só às 6h da manhã
(`"schedule": "0 6 * * *"`), confirmado em `api/main.py:1868-1887`
(comentário explícito: *"Vercel Cron, 1x/dia — limite do plano Hobby"*) e
em `docs/ARQUITETURA.md:353` (conteúdo migrado do README em 14/07/2026).
Verificado (13/07/2026, durante o diagnóstico da
feature de confirmação de coleta) que essa frequência **é a limitação real
do plano Vercel atual** (Hobby restringe cron a no mínimo 1x/dia) — não é
uma divergência acidental de configuração nem um cron mais frequente que
parou de funcionar.

**Risco:** erros pendentes de reprocessamento pelo Resolvedor podem ficar
até 24h sem nova tentativa automática, dependendo da hora em que o erro
ocorreu em relação à janela das 6h.

**Recomendação:** avaliar upgrade de plano Vercel (para permitir cron mais
frequente) antes de uso real com cliente — especialmente relevante para a
futura cascata de realocação por SLA estourado (Fase 2 da feature de
Confirmação de Coleta por E-mail, ver
`ESPECIFICACAO_CONFIRMACAO_COLETA.md`), que depende de execução frequente
para ter valor prático: uma cascata que só reavalia 1x/dia não serve para
destravar coletas paradas em questão de horas.

## Concluído (registro, não pendência)

### Vazamento de dados entre CDs (13/07/2026)
Causa raiz sistêmica: 11+ endpoints (`/api/dashboard`,
`/api/dashboard/coletas-pendentes`, `/api/ondas-hoje`,
`/api/ondas/proximos-dias`, `/api/ondas/historico` e
`/historico/exportar`, `/api/alertas`, `/api/remessas`,
`/api/erros-upload`, `/api/historico`, `/api/relatorio/pdf`, além de
`/api/agentes/status` e `/api/assistente/chat` encontrados na auditoria)
aceitavam `cd_id`/`cd_codigo` como parâmetro vindo do cliente (query
param) em vez de derivar do JWT do usuário autenticado. Um operador
logado num CD conseguia ver dado de outro CD em qualquer uma dessas
telas — revelado em teste manual com os perfis Timoteo (OSA) e Carlos
(ITJ) no fluxo Pendentes → Rastreio Consolidado/Alertas Ativos/Painel
de Diagnóstico.

**Corrigido** via dependency `usuario_autenticado` centralizada
(`api/main.py`) + helpers `cd_codigo_forcado`/`cd_id_forcado`: para
`role=operador` o CD é sempre lido do token, ignorando qualquer valor
que o cliente mande na URL; endpoints de detalhe por id
(`/api/uploads/{id}`, `/api/ondas/{id}/remessas`, `/api/planos/{id}`)
passaram a checar posse do CD e devolver 404 em vez de vazar.

**RECOMENDAÇÃO DE PROCESSO:** todo endpoint novo que retorne dado de
remessa/alerta/histórico/onda **DEVE** usar `usuario_autenticado` +
`cd_codigo_forcado`/`cd_id_forcado` — nunca aceitar `cd_id`/`cd_codigo`
como query param manipulável para `role=operador`. Considerar um teste
automatizado (mesmo que simples) que rode contra todos os endpoints
registrados no `app` e falhe se algum aceitar um `cd_id` que não bata
com o CD do token para `role=operador`, evitando regressão futura —
hoje essa garantia depende só de revisão manual em cada PR.

### Cálculo de motivo de pendência unificado no backend
Antes: `motivoBloqueio(r)` duplicava no frontend (`index.html`) a mesma
lógica de `_pendentes_detalhe()` no backend (`agents/agente_monitor.py`).
Refatorado (10/07/2026): `motivo_pendencia()` em `agents/agente_monitor.py`
é a única fonte de verdade, reaproveitada tanto pelo dashboard quanto por
`GET /api/remessas` (campo `motivo`). Frontend só lê o campo já calculado.
Validado remessa por remessa contra o comportamento anterior — zero
divergências.

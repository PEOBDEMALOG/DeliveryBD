# Dívida Técnica — PEO-BD

## Pendências

### seed_stress_test.py falha de forma reproduzível contra o Supabase de teste — 14/07/2026

**Prioridade:** Média
**Esforço estimado:** M — provavelmente exige quebrar a sessão longa do
script em commits/reconexões periódicas
**Risco:** Médio — bloqueia a única forma documentada de gerar o dataset de
stress completo (`docs/ARQUITETURA.md` cita "27 mil remessas"), então o
checklist de performance fica sem como ser validado contra volume real
até isso ser corrigido

Ao tentar `python scripts/seed_stress_test.py --periodo ano --modo limpo`
(pré-requisito documentado do checklist de stress), a conexão caiu com
`asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in
the middle of operation` — **4 tentativas seguidas falharam** com o mesmo
erro, em pontos completamente diferentes do script a cada vez (uma vez
numa query `INSERT INTO historico_eventos`, outra num `INSERT INTO
onda_remessas`, outra num simples `SELECT` de veículos):

| Tentativa | `--periodo` | Resultado |
|---|---|---|
| 1 | `ano` | Falhou (~4478 remessas geradas antes de cair) |
| 2 | `ano` | Falhou de novo (~4472 remessas) |
| 3 | `trimestre` | Falhou (~4491 remessas) |
| 4 | `mes` | Falhou imediatamente (`SELECT veiculos`, 0 remessas geradas) |
| 5 | `semana` | **Sucesso** — 503 remessas, 60 ondas, 2468 eventos, 299.1s |

Só `--periodo semana` (a menor opção) completou. O padrão (falha em pontos
distintos do script, incluindo uma query de leitura trivial) indica que
não é volume de dado nem uma query específica travando — é a conexão
única e de longa duração que o script mantém aberta sendo derrubada pelo
pooler do Supabase (transaction pooler) depois de alguns minutos, provável
timeout de idle/sessão do lado do Supabase, não um bug de lógica do
script.

**Recomendação:** ajustar `seed_stress_test.py` pra não depender de uma
única conexão viva por dezenas de minutos — reconectar periodicamente
(ex.: a cada período processado) ou fazer commits parciais que permitam
retomar em caso de queda, em vez de uma sessão monolítica.

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

### tests/executar_testes.py — T05 e T09 investigados com dataset completo (semana) — 14/07/2026

Ao validar a reorganização `scripts/executar_testes.py` →
`tests/executar_testes.py` (commit `6151872`), rodei o checklist contra um
banco levemente semeado (110 remessas, acumuladas de testes manuais
anteriores) e encontrei 2 falhas que não batiam com os gargalos já
documentados em `docs/ARQUITETURA.md` (T01/T03/T04/T11). Não consegui
rodar `--periodo ano` (ver "seed_stress_test.py falha de forma
reproduzível", pendência acima), mas consegui `--periodo semana` (503
remessas, dataset gerado pelo próprio `seed_stress_test.py`, não acumulado
manualmente) e rodei o checklist de novo pra comparar:

| Teste | 110 remessas (semeadura manual acumulada) | 503 remessas (`seed_stress_test.py --periodo semana`) |
|---|---|---|
| T05 — OTIF entre 85–100% | ❌ 80.0% | ✅ **97.0%** |
| T09 — `limit=1000` retorna 1000 | ❌ 110 retornados | ❌ 503 retornados |
| T03 — `/api/ondas/historico?periodo=mes` < 4s | ❌ 4.05s | ❌ 4.55s (piorou) |
| T04 — `/api/transportadoras/estatisticas` < 3s | ❌ 4.41s | ❌ 4.66s (piorou) |
| T01 — `/api/dashboard` < 3s | ✅ 2.57s | ✅ 2.18s |
| T11 — `/api/ondas/historico?periodo=ano` < 5s | ✅ 4.19s | ✅ 4.58s (mais perto do limite) |

**Resultado confirmado, não suposição:**
- **T05 estava errado por causa do dataset, não por bug de código.**
  Com dado gerado de forma consistente pelo `seed_stress_test.py`, o OTIF
  voltou pro range esperado (97.0%). O banco levemente semeado tinha
  acumulado cenários propositalmente ruins de testes manuais anteriores
  (`Erros_Controlados`, `Alerta_ATA_Prazo_Vencido` etc.) que derrubavam o
  OTIF artificialmente. **Não é dívida técnica — fechado.**
- **T09 é mesmo um problema do teste, não do código**, confirmado: o
  endpoint `/api/remessas?limit=1000` funcionou corretamente nas duas
  rodadas, retornando exatamente o total disponível sem erro — a asserção
  do checklist ("retorna 1000") assume que existem ≥1000 remessas
  semeadas, o que não aconteceu em nenhuma das duas rodadas (nem a de 503
  chegou lá). Não consegui confirmar com >1000 remessas de verdade por
  causa da falha de conexão documentada na pendência acima. **Não é
  dívida técnica do endpoint — é limitação de dataset do teste, já
  esperada.**
- **T03/T04 se confirmam como gargalo real de N+1**, não só "mesmo
  endpoint documentado" — o tempo piorou de forma consistente com mais
  volume (T03: 4.05s→4.55s; T04: 4.41s→4.66s), o que é exatamente o
  sintoma esperado de N+1 query. Segue como pendência já registrada em
  `docs/ARQUITETURA.md` → "Próximas evoluções" ("Resolver os gargalos de
  N+1 query"), não duplicada aqui.
- **T01/T11 não reproduziram nem a 503 remessas** — continuam passando.
  Não foi possível confirmar se eles falhariam no volume documentado
  originalmente (27 mil remessas, rodada anterior contra Supabase) porque
  a falha de conexão (pendência acima) impediu chegar nesse volume desta
  vez. Fica em aberto — não é uma contradição do achado original (rodado
  outro dia, contra volume ~50x maior), só não pôde ser re-confirmado
  agora.

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

### Path traversal via filename de upload (14/07/2026)
Os 3 endpoints multipart (`/api/upload`, `/api/upload-lote`,
`/api/cotacao/importar`) usavam `arquivo.filename` cru na composição do
path de destino em disco (`UPLOAD_DIR / f"{prefixo}_{arquivo.filename}"`).
Prova de conceito em sandbox isolado (réplica exata da composição antiga):
`filename="/../../db/peo_bd.db"` escapava de `UPLOAD_DIR` e escrevia num
diretório irmão — no ambiente real, o alvo alcançável por esse padrão de
path é o próprio `data/` do projeto (schema, banco SQLite local, etc.).

**Corrigido:** nova função `nome_arquivo_seguro()` (`api/main.py`) — extrai
só o nome base via `Path(...).name` (descarta qualquer `../`/diretório),
normaliza barra invertida (dev local roda em Windows) e aplica whitelist de
caracteres (`[A-Za-z0-9._-]`), preservando a extensão pra não quebrar a
detecção de formato (xlsx/csv/xls) já existente. Usada nos 3 endpoints.
Validado com bateria de payloads (`../`, `/../../`, `..\\..\\`, RFC2231/5987
`filename*` decodificado, null byte, filename vazio) — nenhum escapa de
`UPLOAD_DIR`; filenames legítimos preservados. Teste end-to-end real (não só
função isolada) contra o servidor rodando confirma: ataque bloqueado,
upload legítimo (arquivo xlsx real, pipeline completo) funciona normal.

**RECOMENDAÇÃO DE PROCESSO:** todo endpoint novo que receba `UploadFile` e
grave em disco **DEVE** passar o filename por `nome_arquivo_seguro()` antes
de compor qualquer path — nunca interpolar `arquivo.filename` cru.

### Dependências com CVE conhecido: python-jose e python-multipart (14/07/2026)
Auditoria do Dependabot (repositório `producao`) encontrou 6 vulnerabilidades
conhecidas: `python-jose==3.3.0` (1 crítica — confusão de algoritmo
CVE-2024-33663, não explorável na prática porque o projeto só usa HS256
simétrico com `algorithms=["HS256"]` fixo; 1 moderada — DoS por
descompressão de JWE CVE-2024-33664, alcançável pré-autenticação via
`verificar_token` no header `Authorization`) e `python-multipart==0.0.28`
(1 alta + 3 baixas — DoS de parsing quadrático e RFC2231/5987 filename
smuggling, só alcançáveis autenticado já que os 3 endpoints multipart estão
atrás do middleware JWT).

**Corrigido:** `python-jose` → **3.5.0** (não 3.4.0 como inicialmente
especificado — 3.4.0 corrige os mesmos CVEs mas declara `pyasn1<0.5.0`, o
que força downgrade do `pyasn1` pra uma versão com 2 CVEs de DoS próprios
conhecidos, CVE-2026-30922/CVE-2026-23490; confirmado com
`pip install -r requirements.txt` limpo que a combinação 3.4.0 +
`pyasn1` corrigido é `ResolutionImpossible`. 3.5.0 relaxa pra
`pyasn1>=0.5.0` e resolve limpo). `python-multipart` → **0.0.32** (última
estável — corrige as 4 vulnerabilidades do 0.0.28, patched versions
0.0.30/0.0.31). Suíte de login/JWT (3 credenciais, `/api/auth/me`, rejeição
de token adulterado e de senha errada) e os 3 fluxos de upload com arquivo
legítimo validados contra o servidor com as versões novas — zero falhas.

**RECOMENDAÇÃO DE PROCESSO:** ao seguir uma recomendação de upgrade de
versão vinda de advisory, sempre confirmar com `pip install -r
requirements.txt` num ambiente limpo antes de assumir que a versão
"patched" citada no CVE é a escolha certa — o patch de uma lib pode
empurrar uma dependência transitiva pra uma versão pior.

### Duplicação de BASE_DIR entre api/main.py e core/config.py (14/07/2026)
`api/main.py` calculava `BASE_DIR` de forma independente
(`Path(__file__).resolve().parent.parent`), duplicando exatamente o mesmo
cálculo já feito em `core/config.py`. As duas contas sempre resolviam pro
mesmo valor — mas por coincidência de estrutura de pastas, não porque uma
dependia da outra; um refactor que movesse só um dos dois arquivos
quebraria essa coincidência silenciosamente, sem nenhum teste acusar.
Nunca tinha sido formalmente registrado aqui como pendência — só existia
como comentário no código.

**Corrigido no commit `ab2ee49`**, durante o empacotamento do projeto como
pacote instalável (`pyproject.toml`, `pip install -e .`, ver
`docs/ARQUITETURA.md` → "Setup rápido (local)"): `api/main.py` passou a
importar `BASE_DIR` de `core.config` em vez de recalculá-lo (`FRONTEND_DIR`
derivado do mesmo valor importado). Confirmado `BASE_DIR is core_base_dir
→ True` (mesmo objeto, não só mesmo valor). Validado depois da mudança: dashboard
autenticado como `timoteo`/OSA e `carlos`/ITJ (dados corretos e isolados
por CD), frontend estático servido normalmente (`FRONTEND_DIR` resolvendo
certo), e suíte `tests/testar_offline.py` (Playwright) completa sem
regressão.

### Cálculo de motivo de pendência unificado no backend
Antes: `motivoBloqueio(r)` duplicava no frontend (`index.html`) a mesma
lógica de `_pendentes_detalhe()` no backend (`agents/agente_monitor.py`).
Refatorado (10/07/2026): `motivo_pendencia()` em `agents/agente_monitor.py`
é a única fonte de verdade, reaproveitada tanto pelo dashboard quanto por
`GET /api/remessas` (campo `motivo`). Frontend só lê o campo já calculado.
Validado remessa por remessa contra o comportamento anterior — zero
divergências.

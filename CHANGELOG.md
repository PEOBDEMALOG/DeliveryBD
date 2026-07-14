# Changelog — PEO-BD

[![CI](https://github.com/marciobarbarulo10-oss/Projeto-BD/actions/workflows/ci.yml/badge.svg)](https://github.com/marciobarbarulo10-oss/Projeto-BD/actions/workflows/ci.yml)
— o que o workflow cobre (e não cobre) está em [`docs/CI.md`](docs/CI.md).

Histórico de mudanças relevantes do projeto, agrupado por data/marco de
trabalho. Formato inspirado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)
(seções Added/Changed/Fixed/Security/Docs) — sem versionamento semântico,
já que o projeto não usa tags/releases, só os dois ambientes `origin`/
`producao` (ver `WORKFLOW.md`).

Este arquivo é **retroativo** até `de36ef3` (07/07/2026), primeiro commit
do repositório atual (versões V1-V5 tinham histórico git próprio,
independente — ver `docs/ARQUITETURA.md` → "Histórico de versões").
Commits sem efeito líquido no produto — rotas de debug marcadas
`[TEMPORARIO]` e depois removidas no commit seguinte, mensagem de commit
vazia/placeholder, um redeploy forçado sem mudança de código, um ajuste
intermediário já superado por um commit posterior — foram omitidos aqui
de propósito; o histórico completo continua íntegro no `git log`, só não
vira entrada de changelog por não representar mudança real. Daí para
frente, todo commit relevante ganha uma linha nova aqui — ver
`docs/PROCESSO_SQUAD.md` → "Definição de Pronto".

---

## 2026-07-14 — Auditoria de segurança e reorganização de documentação

### Security
- Corrigido vazamento de dados entre CDs: 11+ endpoints aceitavam
  `cd_id`/`cd_codigo` vindo de parâmetro do cliente em vez de derivar do
  JWT — um operador logado num CD conseguia ver dado de outro CD em
  Rastreio Consolidado, Alertas Ativos e Painel de Diagnóstico.
  (`cbe3170`, `f649007`)
- Corrigido path traversal no upload: os 3 endpoints multipart
  (`/api/upload`, `/api/upload-lote`, `/api/cotacao/importar`) usavam
  `arquivo.filename` cru na composição do path de destino em disco —
  permitia escrever fora do diretório de upload. (`2ab57af`)
- Atualizado `python-jose` (3.3.0 → 3.5.0) e `python-multipart`
  (0.0.28 → 0.0.32): corrige 6 vulnerabilidades conhecidas (1 crítica,
  1 alta, 1 moderada, 3 baixas) encontradas em auditoria do Dependabot.
  (`2ab57af`)

### Changed
- Scripts de validação (`executar_testes.py`, `testar_offline.py`)
  movidos de `scripts/` para `tests/`, separados dos scripts
  operacionais (seed, geração de demo). (`6151872`)
- `README.md` simplificado para conter só o nome do projeto — conteúdo
  completo (arquitetura, setup, deploy) migrado para
  `docs/ARQUITETURA.md`. (`f1a2a29`)
- `api/main.py` deixa de calcular `BASE_DIR` de forma independente e
  passa a importar de `core.config` — as duas contas sempre resolviam
  pro mesmo valor, mas por coincidência, não por dependerem uma da
  outra; agora é uma fonte única de verdade. Duplicação nunca tinha sido
  registrada como pendência formal — ver entrada nova em
  `DIVIDA_TECNICA.md` → "Concluído". (`ab2ee49`)

### Added
- Nova documentação técnica: `docs/ARQUITETURA.md`, `docs/ONBOARDING.md`
  e `docs/PROCESSO_SQUAD.md` (processo de squad/sprint, Definição de
  Pronto, regras de segurança obrigatórias). (`6e73376`, `17a68b6`)
- Adotado Conventional Commits daqui pra frente (`docs/PROCESSO_SQUAD.md`
  → "Convenção de mensagem de commit") e este `CHANGELOG.md`, retroativo
  a todo o histórico do repositório. (`cb8d982`)
- CI básico via GitHub Actions (`.github/workflows/ci.yml`): import de
  sanidade + smoke test/checklist contra SQLite a cada push/PR, com
  `tests/test_smoke.py` como wrapper pytest fino sobre a validação já
  existente. Escopo documentado em `docs/CI.md`. (`f06c664`)
- `requirements.lock.txt`: lockfile de reprodutibilidade exata (via
  pip-tools), fixando `python-jose==3.5.0`/`python-multipart==0.0.32`
  (o upgrade de segurança acima) e toda dependência transitiva. CI passa
  a instalar a partir dele em vez de `requirements.txt` solto. (`3a499cc`)
- `pyproject.toml`: projeto empacotado como pacote Python instalável
  (`pip install -e .`), layout flat — `agents`, `api`, `core` continuam
  nos mesmos caminhos, nenhum import existente mudou. Dependências
  espelham `requirements.txt` dinamicamente (sem duplicar a lista). CI
  passa a validar o empacotamento em todo push. Validado depois: suíte
  `tests/testar_offline.py` (Playwright), login/dashboard manual como
  `timoteo`/OSA e `carlos`/ITJ, e deploy real em `origin` (não só CI) —
  todos sem regressão. Documentado como opção em `docs/ARQUITETURA.md` e
  `docs/ONBOARDING.md` → "Setup rápido (local)". (`ab2ee49`)

### Fixed
- Removidos arquivos soltos não rastreados da raiz e referência obsoleta
  a `demo_presentation/` (pasta que não existe mais) em
  `.vercelignore`. (`6151872`)
- Corrigida entrada de dívida técnica sobre gargalos de performance
  (T05/T09 do checklist de stress) com resultado real de teste contra
  dataset semeado, não suposição. (`8591425`)

---

## 2026-07-13 — Turnaround de coleta e especificação de confirmação por e-mail

### Added
- Novo card "Turnaround de Coleta por Transportadora" no dashboard,
  substitui "Status das Remessas". (`dd4615e`)
- Especificação da feature de confirmação de coleta por e-mail com
  realocação automática (`ESPECIFICACAO_CONFIRMACAO_COLETA.md`),
  revisada com 5 correções depois da primeira versão. (`9dc5341`,
  `3982566`)

### Changed
- Estado "dentro do SLA" no turnaround de coleta passa de cinza pra
  laranja; transportadora sem pendência mostra "tudo em dia" em vez de
  ser omitida da lista. (`3426b31`, `0eea8c9`)
- Removido donut chart de OTIF do dashboard, mantida só a tabela por
  transportadora; marcador de meta virou linha tracejada. (`405583c`,
  `9f7a9a4`)
- Logo BD ajustado iterativamente (proporção do chip, arquivo oficial
  aplicado também na tela de login, emblema Burst no lugar da estrela
  simplificada). (`1923042`, `02555c0`, `ad4a9e9`, `c0e604f`)

### Docs
- Documentado que o cron do Resolvedor rodar só 1x/dia é limitação do
  plano Vercel Hobby, não bug de configuração nem regressão.
  (`0910400`)

---

## 2026-07-12 — Correções de dados e identidade visual

### Fixed
- Corrigido tratamento de células vazias no upload — `NaN` virava a
  string literal `"nan"` e quebrava os campos `is_ata`/`nf_emitida`.
  (`e2f274c`)
- Validado tamanho de `numero_remessa` antes de persistir — evita
  `HTTP 500` cru quando o valor excede o limite da coluna. (`061d742`)

### Changed
- Logo BD recriado como SVG 100% inline, sem dependência de URL
  externa. (`c57c912`)

### Docs
- Documentada dívida técnica: catálogo de `tipos_erro` desalinhado do
  código real — 6 dos 14 tipos catalogados nunca disparam
  organicamente por nenhum caminho de código. (`3e079d5`)

---

## 2026-07-10 — Correções pré-apresentação e primeira rodada de segurança

### Security
- Removidos segredos hardcoded: `JWT_SECRET` passa a falhar
  explicitamente na subida da aplicação se não configurado (em vez de
  um fallback inseguro no código-fonte); credenciais dos 3 usuários de
  demo rotacionadas. (`190628a`)

### Fixed
- Lote de correções pré-apresentação: logo BD em SVG, motivo de
  tentativa/pendente exibido corretamente, card de alerta duplicado
  removido, ondas vazias mostram aviso em vez de tela em branco,
  ajustes no painel de diagnóstico, OTIF por transportadora.
  (`f01f769`)
- Detecção de CD por nome de arquivo substituída por validação de
  schema real + seletor manual obrigatório — evita CD errado inferido
  incorretamente a partir do nome do arquivo. (`af81813`)
- Card duplicado de "Alertas Críticos" removido, grid de KPIs ajustado
  de 7 para 6 colunas. (`38da05a`)
- Corrigido seed com SSL. (`14272da`)

### Changed
- Cálculo de motivo de pendência unificado no backend — antes duplicado
  entre frontend e backend, agora uma única fonte de verdade
  reaproveitada pelo dashboard e por `GET /api/remessas`. (`d7001c1`)

### Docs
- Documentado o fluxo `origin`/`producao` (embrião do que hoje é
  `WORKFLOW.md`). (`14272da`)
- Documentada dívida técnica: senhas hardcoded em `core/auth.py` e
  rotação pendente do `JWT_SECRET` em produção. (`7221868`)

---

## 2026-07-08 a 2026-07-09 — Performance e branding

### Fixed
- Lote de 9 correções pós stress-test: reprocessamento de histórico,
  pendentes/sem-NF no dashboard, barra CSS, distribuição de clientes,
  threshold de FTL, relatórios de transportadora e ondas, testes
  offline via Playwright. (`264c322`)

### Changed
- Performance: índice composto em remessas, cache de 120s em
  estatísticas/histórico-ano, `joinedload` no lugar de N+1 em T02.
  (`045bd4d`)
- Identidade visual BD aplicada ao frontend (cor `#005898` + logo no
  topo da sidebar). (`596cff3`)
- Identificadores do frontend renomeados para alinhar com o checklist
  de testes, sem mudar comportamento. (`7dcd6b3`)

---

## 2026-07-07 — Início do repositório atual (V6)

Primeiro `git init` do projeto nesta pasta — as versões anteriores
(V1–V5) tinham histórico git próprio, independente entre si (ver
`docs/ARQUITETURA.md` → "Histórico de versões"). O primeiro commit já é
a infra de teste de stress, seguido pelo estado completo herdado das
iterações anteriores (agentes, API, frontend, autenticação básica).

### Added
- Infra de teste de stress: seed realista de 6 períodos configuráveis,
  índices de performance, checklist automatizado (T01–T12) — primeiro
  commit deste repositório. (`de36ef3`)
- Estado completo da V6 (Personalização BD) commitado como base do
  repositório atual. (`699a074`)

### Docs
- README reescrito com contexto do projeto e changelog resumido das
  versões V1–V6 (conteúdo hoje vivendo em `docs/ARQUITETURA.md`, após a
  simplificação de 14/07 — ver acima). (`8da4b58`)

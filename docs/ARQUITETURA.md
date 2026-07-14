# Arquitetura — PEO-BD

> Conteúdo migrado do `README.md` em 14/07/2026, quando o README raiz foi
> simplificado para conter só o nome do projeto (decisão de não expor
> descrição, setup, autenticação, infraestrutura de teste e detalhes de
> deploy publicamente). Nada foi perdido — este arquivo é a versão
> completa que antes vivia no README.

## Becton Dickinson · Desafio Inovabra

---

## O que é este projeto

A BD despacha remessas de dois Centros de Distribuição (Osasco/SP, operado pelo
Timóteo, e Itajaí/SC, operado pelo Carlos). Hoje esse planejamento — conferir o
backlog do SAP/UPS WMS, separar por prioridade (contratos ATA, janela de
entrega, hospitais), montar carga (fracionado vs. FTL), notificar a
transportadora certa e acompanhar a entrega até o OTIF fechar — é feito na mão,
em planilha, por pessoa.

O PEO-BD é um "agente robô" que assume esse fluxo de ponta a ponta. Não é um
único modelo de IA fazendo tudo: é um pipeline de 5 agentes especializados
(ingestão → classificação → montagem de ondas → comunicação com
transportadora → monitoramento), uma API que expõe esse pipeline, e um
painel web para o Timóteo, o Carlos e o time de operações usarem no dia a dia.

Este repositório (**V6 — Final, Personalização BD**) é a versão mais recente,
depois de 5 iterações anteriores que evoluíram do MVP local até um deploy real
em produção (Vercel + Supabase), com autenticação, cotação de transportadoras,
histórico de ondas, assistente de diagnóstico, modo de contingência offline e,
mais recentemente, uma infraestrutura própria de teste de carga.

---

## Stack técnica

**Backend** — Python 3.11+, FastAPI (API REST + middleware de auth),
SQLAlchemy 2.0 assíncrono (`asyncpg` em produção/Postgres, `aiosqlite` em
dev local), Pydantic (schemas de request/response), `python-jose` (JWT
HS256), `python-multipart` (upload), `pandas`/`openpyxl`/`xlrd` (leitura de
planilhas SAP/UPS WMS), `reportlab` (geração de PDF server-side), SDK da
Anthropic (Assistente de Diagnóstico), envio de e-mail via SMTP e/ou Resend.
Lista completa e versões exatas em `requirements.txt`.

**Frontend** — SPA em arquivo único (`frontend/index.html`), sem build step:
Alpine.js (reatividade/estado), Tailwind CSS via CDN (estilo), SheetJS/`xlsx.js`
(leitura de planilha client-side no Modo de Contingência) e jsPDF (geração de
PDF no cliente em alguns fluxos, complementar ao `reportlab` do backend).

**Banco** — Postgres (Supabase) em produção e teste; SQLite local como
fallback só para desenvolvimento rápido (não suportado no Vercel).

**Infra/deploy** — Vercel (serverless, `@vercel/python`), Vercel Cron para o
Agente Resolvedor. Ver `WORKFLOW.md` para os dois ambientes (`origin`/
`producao`) e a regra de promoção entre eles.

---

## Estrutura do projeto

```
peo_bd/
├── agents/
│   ├── agente_ingestor.py       # Agente 1 — lê upload, normaliza, deduplica
│   ├── agente_classificador.py  # Agente 2 — alertas ATA, janela, NF, consolidação
│   ├── agente_montador.py       # Agente 3 — monta ondas, dimensiona carga
│   ├── agente_comunicador.py    # Agente 4 — gera xlsx e envia e-mail por transportadora
│   ├── agente_monitor.py        # Agente 5 — rastreio consolidado, dashboard, OTIF
│   ├── agente_resolvedor.py     # Agente 6 — varredura de erros/pendências (cron diário)
│   ├── agente_assistente.py     # Assistente de Diagnóstico (chat contextual via Claude)
│   └── orquestrador.py          # Coordena os agentes em pipeline
├── api/
│   └── main.py                  # API REST FastAPI (upload, dashboard, rastreio,
│                                 # cotação, histórico, auth, cron do Resolvedor)
├── core/
│   ├── auth.py                  # JWT stateless — usuários demo (Timóteo/Carlos/Erick)
│   ├── config.py                # Configurações, .env, resolução de DATABASE_URL
│   ├── models.py                # Modelos ORM SQLAlchemy
│   ├── historico.py             # Serviço de registro de eventos no histórico
│   └── email_service.py         # Envio de e-mail (SMTP + Resend p/ alertas internos)
├── data/
│   ├── schema.sql                # Schema SQL completo (referência)
│   ├── demo/                     # Planilhas de demonstração (SAP/UPS)
│   ├── uploads/, db/, outputs/   # Gerados em runtime (git-ignorados)
├── frontend/
│   ├── index.html                # Painel web (SPA em arquivo único)
│   └── contingencia-db.js        # Modo de contingência offline (leitura client-side)
├── scripts/
│   ├── seed_demo.py               # Popula CDs, clientes, transportadoras, remessas demo
│   ├── seed_tipos_erro.py / seed_erro_acoes.py   # Catálogo de erros do Resolvedor
│   ├── seed_transportadoras.py    # Ajusta meta_otif por transportadora
│   ├── criar_indexes.py           # Índices de performance (Etapa 1 do stress test)
│   ├── seed_stress_test.py        # Gera histórico realista em volume (Etapa 2)
│   └── gerar_backlog_demos.py / gerar_planilha_cotacao.py / simular_erros_demo.py
├── tests/
│   ├── executar_testes.py         # Checklist automatizado de performance/corretude (Etapa 3)
│   ├── testar_offline.py          # Suíte Playwright do Modo de Contingência offline
│   └── test_smoke.py              # Wrapper pytest fino sobre os dois acima (usado pelo CI)
├── .github/workflows/ci.yml       # CI: import de sanidade + smoke test (ver docs/CI.md)
├── requirements.txt
├── vercel.json                    # Deploy serverless (build + cron do Resolvedor)
└── README.md
```

---

## Setup rápido (local)

### 1. Pré-requisitos
- Python 3.11+
- Um banco Postgres acessível (Supabase é o usado em produção) — ou deixe
  `DATABASE_URL` vazio para cair em SQLite local (só para testes rápidos, o
  Vercel não usa SQLite em produção).

### 2. Instalar dependências
```bash
pip install -r requirements.txt
```

### 3. Configurar `.env`
Copie `.env.example` para `.env` e preencha pelo menos:
```env
DATABASE_URL=postgresql://postgres:SENHA@db.SEU_PROJETO.supabase.co:5432/postgres
JWT_SECRET=gere_com_openssl_rand_-hex_32
```
`JWT_SECRET` é obrigatório — a aplicação falha ao subir sem ele (`core/config.py`).
Gere um valor único por ambiente (`openssl rand -hex 32`); nunca reaproveite
entre dev/teste/produção.
(SMTP, Resend e ANTHROPIC_API_KEY são opcionais — sem eles o envio de e-mail
roda em modo sem efeito e o Assistente de Diagnóstico fica desabilitado.)

### 4. Popular dados de demonstração
```bash
python scripts/seed_demo.py
python scripts/seed_tipos_erro.py
python scripts/seed_erro_acoes.py
python scripts/seed_transportadoras.py
```

### 5. Subir a API
```bash
# Windows: mata a porta 8000 se já estiver em uso e sobe com --reload
./start.ps1
# ou diretamente:
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

- **API docs (Swagger):** http://localhost:8000/docs
- **Painel web:** http://localhost:8000/ (serve `frontend/index.html`)
- **Health check:** http://localhost:8000/health · **Ping sem banco:** `/api/ping`

---

## Autenticação

Toda rota `/api/*` exige um JWT (Bearer token), exceto `/api/auth/login`,
`/api/ping` e o cron do Resolvedor (`/api/resolvedor/executar`, protegido por
`CRON_SECRET` em vez de login). Usuários de demonstração e suas senhas estão
definidos em `core/auth.py` — credenciais não ficam nesta documentação; peça ao
responsável do projeto ou consulte esse arquivo diretamente.

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"usuario":"<usuario>","senha":"<senha>"}'
# → {"token": "...", ...}  — use como "Authorization: Bearer <token>" nas demais chamadas
```

---

## Arquitetura dos agentes (7 ao todo)

6 agentes formam o pipeline sequencial disparado por upload; o 7º
(Assistente) roda à parte, sob demanda.

```
[UPLOAD CSV/XLSX]
       │
       ▼
┌─────────────────┐
│  Agente 1       │  Lê arquivo, normaliza colunas SAP/UPS,
│  INGESTOR       │  calcula hash SHA-256, detecta duplicatas,
│                 │  persiste remessas no banco
└────────┬────────┘
         ▼
┌─────────────────┐
│  Agente 2       │  Aplica regras de negócio BD:
│  CLASSIFICADOR  │  ATA crítico, NF pendente, janela vencida,
│                 │  oportunidade FTL fracionado→consolidado
└────────┬────────┘
         ▼
┌─────────────────┐
│  Agente 3       │  Agrupa por região, ordena por prioridade+janela,
│  MONTADOR       │  aloca veículo, determina FTL/fracionado,
│                 │  cria ondas de separação no banco
└────────┬────────┘
         ▼
┌─────────────────┐
│  Agente 4       │  Gera planilha Excel por transportadora,
│  COMUNICADOR    │  monta e-mail estruturado, envia via SMTP,
│                 │  registra protocolo de confirmação
└────────┬────────┘
         ▼
┌─────────────────┐
│  Agente 5       │  Consolida rastreio, gera alertas de SLA,
│  MONITOR        │  atualiza OTIF, alimenta o dashboard em tempo real
└────────┬────────┘
         ▼
┌─────────────────┐
│  Agente 6       │  Varredura diária (Vercel Cron) de erros e
│  RESOLVEDOR     │  pendências; escala para humano quando necessário
└─────────────────┘
```
**Agente 7 — ASSISTENTE** (`agente_assistente.py`): chat contextual via
Claude, acionado sob demanda (não roda no pipeline automático). Recebe o
tipo de erro em foco no Painel de Diagnóstico (ou nenhum, no botão flutuante
geral) mais um snapshot do estado do sistema — já filtrado pelo CD do
usuário — e responde perguntas em linguagem natural sobre o que aconteceu e
o que fazer a respeito.

---

## Ambientes e promoção para produção

O projeto roda em dois ambientes com repositórios git remotos separados
(`origin` para teste, `producao` para o ambiente real usado em
apresentações) — cada um com seu próprio deploy Vercel e banco Supabase
independente. Toda mudança começa em `origin`; a promoção para `producao`
é manual e só acontece após validação explícita.

A tabela dos dois ambientes, as regras de promoção e os comandos exatos
vivem só em `WORKFLOW.md` (não duplicados aqui) — inclusive o checklist
obrigatório de teste de vazamento entre CDs antes do primeiro upload real
em produção.

---

## Infra de teste de stress (Etapas 1–3)

Para validar a aplicação sob volume realista (um ano de operação dos dois
CDs) antes de qualquer apresentação, foi construída uma infra de 3 etapas:

1. **`scripts/criar_indexes.py`** — cria os índices de performance usados
   pelas queries mais frequentes de remessas e histórico de eventos.
   Idempotente, roda antes de qualquer seed.
2. **`scripts/seed_stress_test.py`** — gera histórico de uso diário realista
   (remessas, ondas, eventos, alertas, erros) via bulk insert, para um período
   configurável:
   ```bash
   python scripts/criar_indexes.py
   python scripts/seed_stress_test.py --periodo ano --modo limpo
   # periodos: semana | mes | trimestre | semestre | nove_meses | ano
   ```
3. **`tests/executar_testes.py`** — checklist automatizado (T01–T12) que
   roda contra a API de pé (local ou deployada), mede tempo de resposta e
   valida performance, corretude e capacidade:
   ```bash
   python tests/executar_testes.py --base-url http://localhost:8000 --periodo ano
   ```
   Gera um relatório do tipo:
   ```
   RESULTADO: 6/12 testes passaram
   GARGALOS IDENTIFICADOS: T01, T02, T03, T04, T11
   ```

A última rodada contra um ano de dados (27 mil remessas) no Supabase expôs
gargalos reais de N+1 query em `/api/dashboard`, `/api/ondas/historico` e
`/api/transportadoras/estatisticas` (ainda não otimizados) e um bug real no
seed (`transportadora_id` não gravado nos eventos do Comunicador — corrigido).

---

## Deploy (Vercel + Supabase)

- `vercel.json` builda `api/main.py` com `@vercel/python`, inclui
  `frontend/`, `core/`, `agents/`, `data/demo/` e `scripts/` no bundle, e
  agenda o cron do Resolvedor 1x/dia (limite do plano Hobby).
- Em produção, `DATABASE_URL` aponta para o Supabase (Postgres). O
  `core/config.py` normaliza a URL (`postgres://` → `postgresql+asyncpg://`),
  remove parâmetros incompatíveis com `asyncpg` e configura SSL + desativa o
  cache de prepared statements (exigido pelo Supabase Transaction Pooler).
- O filesystem do Vercel é somente-leitura fora de `/tmp` — uploads e outputs
  gerados em runtime vão para `/tmp` quando `VERCEL=1`.

Nenhuma mudança de código é necessária entre ambientes — só variáveis de ambiente.

---

## Histórico de versões

O projeto evoluiu por 6 iterações, cada uma em sua própria pasta local
(`Versão V1` … `Versão V6`):

### V1 — MVP local
Primeira versão funcional: os 5 agentes originais, API FastAPI, SQLite local,
frontend simples em HTML. Sem autenticação, sem deploy em nuvem — só validar
o pipeline ingestão → montagem → comunicação → monitoramento rodando na
máquina local.

### V2 — Ajustes de MVP
Correções no pipeline (`main.py`, `models.py`, `seed_demo.py`) e no frontend,
mais dados de demonstração com backlog. Introduz `start.ps1` (sobe a API
liberando a porta 8000 automaticamente) e os primeiros logs de servidor.

### V3 — Preparação para nuvem (idas e vindas com Vercel)
Primeira versão com histórico git real. Muita iteração de infraestrutura:
correções de bugs de dashboard/ingestor/ondas, deduplicação de remessa,
geração de histórico completo no seed, e uma sequência longa de ajustes para
rodar no Vercel (versões de `asyncpg`/SQLAlchemy, SSL, `channel_binding`,
`vercel.json`). Termina revertendo o Vercel para focar numa apresentação
local primeiro (`chore: remover Vercel e limpar dependências`).

### V4 — Vercel
Volta ao objetivo de deploy em nuvem e fecha o ciclo: startup resiliente sem
`DATABASE_URL` definida, detecção do ambiente Vercel via path `/var/task`,
compatibilidade `asyncpg`/SQLAlchemy 2.0 (banco Neon), endpoint
`POST /api/demo/reset` para limpar dados operacionais entre demos. Termina em
"PEO-BD pronto para deploy no Vercel".

### V5 — Melhor Versão
A maior expansão de funcionalidades do projeto:
- Migração do banco de Neon para **Supabase** (SSL, pgbouncer, monkey-patches
  de `asyncpg` para o Transaction Pooler, depois removidos em favor de
  `connect_args` explícito).
- **Histórico de ondas por período** e fechamento de onda por romaneio.
- **Painel de Diagnóstico** (renomeado de "Erros de Upload") com **Assistente
  de Diagnóstico** — chat contextual por tipo de erro.
- **Modo de Contingência offline** — leitura de planilha client-side via
  SheetJS quando a API está fora do ar.
- Cotação de transportadoras, filtro de transportadora no rastreio, cards
  clicáveis no dashboard, oportunidades FTL expansíveis, correção de
  visibilidade pública de eventos, auto-refresh via Chart.js.

### V6 — Final (Personalização BD)
Versão atual. Parte da base do V5 e adiciona:
- **Autenticação JWT** (`core/auth.py` + middleware global em `api/main.py`)
  com 3 usuários demo (Timóteo, Carlos, Erick/admin) e rotas `/api/auth/login`
  e `/api/auth/me`.
- **Gestão de meta de OTIF por transportadora** (`PATCH
  /api/transportadoras/{id}/meta-otif` + `scripts/seed_transportadoras.py`).
- **Suporte a `semestre` e `ano` em `/api/ondas/historico`** (antes o período
  máximo era trimestre).
- **Infra de teste de stress completa** (Etapas 1–3, ver seção acima):
  índices de performance, seed de histórico realista por período configurável
  e checklist automatizado de performance/corretude/capacidade.
- Primeiro `git init` do projeto nesta pasta (as versões V3–V5 tinham git
  próprio, independente entre si).

---

## Próximas evoluções

| Prioridade | Feature |
|------------|---------|
| Alta | Resolver os gargalos de N+1 query identificados pelo checklist de stress (T01, T03, T04, T11) |
| Alta | Scheduler mais frequente para verificação de SLA (hoje limitado a 1x/dia pelo cron do plano Hobby) |
| Média | Engine de IA generativa para análise de padrões de consolidação (além do Assistente de Diagnóstico) |
| Média | Integração com roteirizador próprio de SP já existente |
| Baixa | Scraping de portais DHL/UPS para rastreio automático sem API |
| Baixa | Módulo de análise de clientes potenciais (interface para time de vendas) |

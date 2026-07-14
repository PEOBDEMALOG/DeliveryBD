# Onboarding — PEO-BD

Guia para quem está entrando no projeto agora. Não duplica o setup
detalhado nem a lista de dependências — isso já vive em
[`ARQUITETURA.md`](ARQUITETURA.md) (seções "Stack técnica" e "Setup rápido
(local)"). Aqui: o checklist prático pra sair do zero rodando, as
convenções de código já em uso, como validar seu trabalho, e o que ler
antes de mexer em áreas sensíveis.

---

## 1. Setup local — checklist rápido

Passo a passo completo está em `ARQUITETURA.md` → "Setup rápido (local)".
Resumo do fluxo:

1. Python 3.11+, `pip install -r requirements.lock.txt` (versão exata,
   reproduz CI/produção — `requirements.txt` é só a lista de
   dependências diretas, usada pra atualizar versão deliberadamente).
2. Copiar `.env.example` → `.env` e preencher (nomes das variáveis abaixo —
   **nunca peça os valores reais por chat/mensagem**; peça ao responsável
   do projeto ou copie do cofre de credenciais que a equipe usa).
3. Rodar os scripts de seed (`scripts/seed_demo.py` e os 3 seguintes,
   listados em `ARQUITETURA.md`).
4. Subir a API (`./start.ps1` no Windows, ou `uvicorn api.main:app --reload`).
5. Login em `POST /api/auth/login` com um dos 3 usuários demo definidos em
   `core/auth.py` (credenciais não ficam em nenhuma documentação — peça
   diretamente).

### Variáveis de ambiente necessárias (nomes, sem valores)

| Variável | Obrigatória? | Para quê |
|---|---|---|
| `DATABASE_URL` | Não (fallback SQLite local) | Postgres/Supabase. Vazio = SQLite local (não funciona no Vercel). |
| `JWT_SECRET` | **Sim — app não sobe sem ela** | Assina/valida os tokens JWT. Gerar com `openssl rand -hex 32`, único por ambiente. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `EMAIL_FROM` | Não | Envio de programação de coleta às transportadoras. Sem isso, envio roda em modo sem efeito. |
| `RESEND_API_KEY` / `EMAIL_ALERTAS_EMALOG` | Não | Alertas internos por e-mail (erros críticos). |
| `ANTHROPIC_API_KEY` | Não | Habilita o Assistente de Diagnóstico. Sem ela, o chat fica desabilitado (resto do sistema funciona normal). |
| `CRON_SECRET` | Não (tem default de dev) | Protege `/api/resolvedor/executar` no cron do Vercel. |

Referência completa (com comentários) em `.env.example` — mantenha esse
arquivo atualizado sempre que uma variável nova for adicionada.

---

## 2. Estrutura de pastas

A árvore completa e comentada está em `ARQUITETURA.md` → "Estrutura do
projeto". Resumo mental: `agents/` (os 7 agentes + orquestrador), `api/`
(rotas FastAPI, um único `main.py`), `core/` (auth, config, models,
serviços compartilhados), `frontend/` (SPA em arquivo único, sem build),
`scripts/` (seed, stress test, geração de demo), `data/` (schema SQL de
referência + o que é gerado em runtime).

---

## 3. Convenções de código já em uso

Levantadas lendo o código existente — siga o padrão, não introduza um
estilo novo numa PR isolada:

- **Identificadores em português**, incluindo nomes de função, variável e
  parâmetro (`criar_token`, `verificar_token`, `listar_remessas`,
  `usuario_autenticado`, `cd_codigo_forcado`). Comentários também em
  português.
- **Cabeçalho de arquivo padrão**: primeira linha `# peo_bd/<caminho/do/arquivo.py>`,
  seguida de uma linha curta descrevendo a responsabilidade do módulo.
- **Separadores de seção** dentro de arquivos grandes (`api/main.py`,
  `core/config.py`): `# ── NOME DA SEÇÃO ─────────────────────────────`
  (traço longo até ~80 colunas) para dividir blocos de rotas/config
  relacionados.
- **Comentários explicam o porquê, não o quê** — identificadores bem
  nomeados já dizem o que o código faz; comentário só quando há uma
  decisão não óbvia, uma limitação de infra, ou um comportamento que
  surpreenderia quem só lê a assinatura da função. Evite comentário que só
  repete o nome da variável/função em prosa.
- **Type hints sempre** em assinatura de função (`Optional[str]`,
  `AsyncSession`, `dict`, etc.) e `async`/`await` em toda I/O (banco,
  e-mail, chamada à API da Anthropic).
- **SQLAlchemy 2.0 style** (`select(Model).where(...)`, não a API antiga
  de `Query`), sempre via `AsyncSession` injetada por
  `Depends(get_db)`.
- **Dependency injection do FastAPI** para tudo que é transversal —
  sessão de banco (`Depends(get_db)`) e, desde a correção de vazamento de
  CD (ver `DIVIDA_TECNICA.md`), também o usuário autenticado
  (`Depends(usuario_autenticado)`). Não reinvente a decodificação do JWT
  numa rota nova — sempre passe pela dependency.
- **Sem framework de testes unitários (pytest/unittest)** — a validação é
  feita por scripts dedicados que rodam contra a API de pé (seção
  seguinte), não por testes isolados de função. Se for adicionar testes
  unitários de verdade, é uma decisão de arquitetura nova, não um padrão
  já estabelecido — discuta antes.

---

## 4. Como validar seu trabalho (tests/ e scripts/)

Não há `pytest`/`unittest` no projeto. O que existe hoje:

- **`tests/executar_testes.py`** — checklist automatizado (T01–T12) de
  performance/corretude/capacidade, roda contra a API já de pé (local ou
  deployada):
  ```bash
  python tests/executar_testes.py --base-url http://localhost:8000 --periodo ano
  ```
  Detalhe completo (junto com `criar_indexes.py` e `seed_stress_test.py`,
  que preparam o volume de dados usado por esse checklist) em
  `ARQUITETURA.md` → "Infra de teste de stress".

- **`tests/testar_offline.py`** — suíte de validação do Modo de
  Contingência offline: sobe uma instância isolada do backend (SQLite em
  pasta temporária) e dirige um Chromium real via Playwright, simulando
  quedas de conexão reais e interceptadas. Rode antes de mexer em
  `frontend/contingencia-db.js` ou na lógica de detecção de conexão do
  frontend.

- **Scripts de seed/demo** (`seed_demo.py`, `seed_stress_test.py`,
  `gerar_demos.py`, `gerar_backlog_demos.py`, `simular_erros_demo.py`,
  `seed_historico_demo.py`) — geram dado sintético para testar
  manualmente um fluxo específico sem precisar de upload real. Use o mais
  próximo do cenário que você está validando em vez de escrever um script
  novo.

- **Validação manual multi-perfil** — para qualquer mudança que toque
  filtro por CD, autenticação ou dado exibido por tela, valide logado como
  os dois operadores (`timoteo`/CD Osasco, `carlos`/CD Itajaí), não só
  como admin. É assim que o vazamento de dados entre CDs foi encontrado
  originalmente (ver `DIVIDA_TECNICA.md`) — admin sozinho não teria
  revelado o bug.

Antes de promover qualquer mudança para `producao`, siga o processo em
`WORKFLOW.md` (validação em `origin` primeiro, sempre).

---

## 5. Leitura obrigatória antes de mexer em áreas sensíveis

Leia [`DIVIDA_TECNICA.md`](../DIVIDA_TECNICA.md) inteiro antes de tocar em:

- **Autenticação / JWT** (`core/auth.py`, middleware `exigir_jwt` em
  `api/main.py`) — inclui a pendência de senhas em texto puro e a rotação
  de `JWT_SECRET` pendente em produção.
- **Qualquer endpoint que devolva dado de remessa/alerta/histórico/onda
  filtrado por CD** — inclui o registro completo do vazamento de dados
  entre CDs (13/07/2026) e a regra de arquitetura obrigatória daqui pra
  frente (ver também [`PROCESSO_SQUAD.md`](PROCESSO_SQUAD.md) → "Regras de
  segurança obrigatórias").
- **`tipos_erro` / `AgenteResolvedor`** — o catálogo de erros está
  desalinhado do código real (6 dos 14 tipos nunca disparam
  organicamente); não assuma que um tipo catalogado tem gatilho real sem
  conferir.
- **Cron do Resolvedor** — roda só 1x/dia por limitação do plano Vercel
  Hobby, não é bug nem configuração incompleta.

`DIVIDA_TECNICA.md` é atualizado toda vez que uma dívida nova é encontrada
ou uma existente é resolvida — é a fonte de verdade sobre "o que já se
sabe que está torto" no projeto. Não repita esse levantamento do zero.

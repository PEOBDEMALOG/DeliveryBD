# CI — PEO-BD

[![CI](https://github.com/marciobarbarulo10-oss/Projeto-BD/actions/workflows/ci.yml/badge.svg)](https://github.com/marciobarbarulo10-oss/Projeto-BD/actions/workflows/ci.yml)

`.github/workflows/ci.yml` roda automaticamente a cada `push` em `main`
(só em `origin` — `producao` não tem esse workflow, só recebe o que já
passou por `origin`, ver `WORKFLOW.md`) e em qualquer pull request contra
`main`. Dois jobs, sequenciais (`smoke-e-checklist` só roda se `lint`
passar):

## O que o CI cobre

### Job 1 — `lint` (import de sanidade)
Instala `config/requirements.lock.txt` (versão exata, mesma usada em produção —
não `config/requirements.txt` solto, ver `ARQUITETURA.md` → "Setup rápido"),
depois `pip install -e .` (valida que `pyproject.toml` continua
empacotando `agents`/`api`/`core` corretamente), e importa todo módulo de
`core/`, `agents/` e `api/`. Pega erro de sintaxe, import quebrado
(dependência faltando, nome errado, ciclo de import) ou regressão no
empacotamento **antes** de gastar tempo subindo servidor. Não toca banco.

### Job 2 — `smoke-e-checklist`
Sobe a API de verdade contra SQLite (o fallback padrão do projeto quando
`DATABASE_URL` não está definida — o workflow não define essa variável de
propósito), semeia com `scripts/seed_demo.py`, e roda
`pytest tests/test_smoke.py`, que cobre duas coisas:

1. **Smoke test mínimo, como asserção pytest real** — `GET /api/ping`
   retorna 200, login com usuário demo retorna 200 com token, `GET
   /api/dashboard` autenticado retorna 200. Se qualquer um desses
   quebrar, o job falha (vermelho).
2. **Checklist de `tests/executar_testes.py`, invocado como subprocesso
   e só reportado** — não bloqueia o job. Ver "O que o CI NÃO cobre"
   abaixo pra entender por quê.

## O que o CI NÃO cobre (de propósito)

- **Não substitui `tests/testar_offline.py`** — a suíte Playwright do
  Modo de Contingência não roda no CI (exige browser real, mais pesado e
  mais lento do que faz sentido rodar em todo push). Continua sendo
  responsabilidade de quem mexe em `frontend/contingencia-db.js` ou na
  lógica de detecção de conexão rodar manualmente antes de promover —
  ver `docs/ONBOARDING.md` seção 4.
- **Não substitui a validação manual multi-perfil** — qualquer mudança
  que toque autenticação, filtro por CD, ou dado exibido por tela
  continua exigindo login manual como os dois operadores (Timóteo/Osasco
  e Carlos/Itajaí), não só admin. O CI só usa o usuário `timoteo` pro
  smoke test; ele não teria pego sozinho o vazamento de dados entre CDs
  documentado em `DIVIDA_TECNICA.md` — ver `docs/ONBOARDING.md` →
  "Validação manual multi-perfil" e `docs/PROCESSO_SQUAD.md` →
  Definição de Pronto.
- **O resultado de `tests/executar_testes.py` no CI é informativo, não
  um gate.** Com o dataset leve gerado por `scripts/seed_demo.py` (em
  vez do dataset de stress completo de `scripts/seed_stress_test.py
  --periodo ano`), vários testes do checklist falham por construção, não
  por bug: `T09` (retorna 1000 remessas) nunca passa porque o seed leve
  não chega a 1000 linhas; `T07`/`T10` (transportadora com
  histórico/filtro) dependem de dado que só o `seed_stress_test.py`
  gera. Isso já foi investigado e confirmado em `DIVIDA_TECNICA.md` (ver
  entrada "T05 e T09 investigados com dataset completo") — bloquear o CI
  nesses testes geraria falso vermelho em todo push, não sinal real de
  regressão.
- **Não roda contra Postgres/Supabase** — só contra SQLite. A lógica de
  conexão específica do Supabase (SSL, prepared statements do
  Transaction Pooler, ver `core/config.py`) não é exercitada pelo CI.
- **Não valida deploy no Vercel** — só que o código sobe localmente. Ver
  `docs/PROCESSO_SQUAD.md` → Definição de Pronto pro processo completo de
  validação em `origin` antes de promover pra `producao`.

## Rodando o mesmo checklist localmente

```bash
python scripts/seed_demo.py
uvicorn api.main:app &
pytest tests/test_smoke.py -v
```

Pra validação completa antes de promover pra produção, ver
`docs/ONBOARDING.md` seção 4 e `docs/PROCESSO_SQUAD.md` → Definição de
Pronto — o CI é uma rede de segurança em todo push, não o critério final
de "pronto".

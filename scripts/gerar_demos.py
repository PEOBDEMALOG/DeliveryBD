"""
Gera os 6 arquivos de demonstração para o PEO-BD:
  • Backlog_SAP_OSA_DDMMYYYY.xlsx        — 80 remessas (backlog Osasco)
  • Backlog_UPS_WMS_ITJ_DDMMYYYY.xlsx    — 40 remessas (backlog Itajaí)
  • Remessas_SAP_OSA_DDMMYYYY.xlsx       — 30 remessas (demo OSA mix)
  • Remessas_SAP_OSA_ATA_DDMMYYYY.xlsx   — 25 remessas (demo OSA foco ATA)
  • UPS_WMS_Export_ITJ_DDMMYYYY.xlsx     — 20 remessas (demo ITJ mix)
  • UPS_WMS_Export_ITJ_ATA_DDMMYYYY.xlsx — 20 remessas (demo ITJ foco ATA)

Execute: python scripts/gerar_demos.py
"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Semente fixa para dados reproduzíveis
random.seed(2026)

today    = date.today()
DATA_STR = today.strftime("%d%m%Y")
DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
DEMO_DIR.mkdir(parents=True, exist_ok=True)

# ─── Clientes SP ──────────────────────────────────────────────────────────────

CAP_SP = [
    ("Hospital das Clínicas FMUSP",     "São Paulo"),
    ("Hospital Albert Einstein",         "São Paulo"),
    ("Hospital Sírio-Libanês",           "São Paulo"),
    ("Hospital Oswaldo Cruz",            "São Paulo"),
    ("Fleury Medicina Diagnóstica SP",   "São Paulo"),
    ("Hermes Pardini SP",                "São Paulo"),
    ("Delboni Auriemo SP",               "São Paulo"),
    ("Hospital Samaritano SP",           "São Paulo"),
    ("Hospital Beneficência Portuguesa", "São Paulo"),
    ("Hospital Santa Catarina SP",       "São Paulo"),
]

INT_SP = [
    ("Hospital Municipal de Campinas",   "Campinas"),
    ("Hospital das Clínicas UNICAMP",    "Campinas"),
    ("Hospital Regional de Sorocaba",    "Sorocaba"),
    ("Hospital Municipal Ribeirão Preto","Ribeirão Preto"),
    ("Hospital das Clínicas Botucatu",   "Botucatu"),
]

# ─── Clientes SC ──────────────────────────────────────────────────────────────

SC_CLI = [
    ("Hospital Regional de Itajaí",        "Itajaí"),
    ("Hospital Municipal de Blumenau",     "Blumenau"),
    ("UFSC Hospital Universitário",        "Florianópolis"),
    ("Hospital Infantil Joana de Gusmão",  "Florianópolis"),
    ("Hospital e Maternidade São José",    "Joinville"),
    ("Hospital Regional Alto Vale",        "Rio do Sul"),
    ("Hospital Mariápolis",                "Balneário Camboriú"),
    ("Hospital e Maternidade Jaraguá",     "Jaraguá do Sul"),
    ("Hospital Geral de Joinville",        "Joinville"),
    ("Hospital São José Criciúma",         "Criciúma"),
]

# ─── Janelas de entrega ───────────────────────────────────────────────────────
# Valores ≤ 2 h de intervalo → janela crítica → prioridade "alta"

JANELAS = [
    "08h-12h",   # 4 h  → normal
    "08h-12h",
    "13h-17h",   # 4 h  → normal
    "13h-17h",
    "09h-11h",   # 2 h  → crítica
    "07h-09h",   # 2 h  → crítica
    "14h-16h",   # 2 h  → crítica
    "Qualquer",
    "Qualquer",
    "Qualquer",
]

SERVICOS_UPS = ["EXPRESS", "EXPRESS", "STANDARD", "STANDARD", "PRIORITY"]


# ─── Helpers de geração ───────────────────────────────────────────────────────

def _vol():   return round(random.uniform(0.08, 3.8), 2)
def _peso():  return round(random.uniform(3.5, 310.0), 1)
def _valor(): return round(random.uniform(900, 115000), 2)
def _qtd():   return random.randint(1, 8)


def sap_row(num: int, cliente: str, cidade: str,
            is_ata: bool = False, is_critica: bool = False) -> dict:
    janela = random.choice(JANELAS)
    if is_critica:
        empenho = f"EMP{num:05d}"
        prazo   = (today + timedelta(days=random.randint(1, 4))).isoformat()
        status  = "ATA Aprovado"
    elif is_ata:
        empenho = f"EMP{num:05d}"
        prazo   = (today + timedelta(days=random.randint(6, 20))).isoformat()
        status  = "Empenho Liberado"
    else:
        empenho = ""
        prazo   = ""
        status  = random.choice(["Aprovado", "Liberado", "Em processamento"])
    return {
        "Remessa":      str(num),
        "Cliente":      cliente,
        "Cidade":       cidade,
        "Vol (m³)":     _vol(),
        "Peso (kg)":    _peso(),
        "Valor NF":     _valor(),
        "Janela":       janela,
        "Status":       status,
        "Num.Empenho":  empenho,
        "Prazo.Empenho":prazo,
        "NF":           f"{num:06d}",
        "Qtd.Volumes":  _qtd(),
    }


def ups_row(num: int, cliente: str,
            is_ata: bool = False, is_critica: bool = False) -> dict:
    servico = "PRIORITY" if is_critica else ("EXPRESS" if is_ata else random.choice(SERVICOS_UPS))
    if is_critica:
        sla = "ATA - 24h URGENTE"
    elif is_ata:
        sla = "ATA - 48h"
    else:
        sla = random.choice(["2 dias", "3 dias", "24h", "48h", "5 dias"])
    ups_id = f"1Z{num:08X}"
    return {
        "ID_UPS":       ups_id,
        "Destinatário": cliente,
        "UF":           "SC",
        "Serviço":      servico,
        "Peso":         round(random.uniform(2.0, 175.0), 1),
        "Volumes":      random.randint(1, 5),
        "NF":           f"{num:06d}",
        "Prazo SLA":    sla,
    }


def save(rows: list, name: str):
    df   = pd.DataFrame(rows)
    path = DEMO_DIR / name
    df.to_excel(path, index=False, engine="openpyxl")
    print(f"  OK {name}  ({len(rows)} remessas)")
    return path


# ─── Remove arquivos antigos ──────────────────────────────────────────────────
removed = 0
for ext in ("*.xlsx", "*.xls", "*.csv"):
    for f in DEMO_DIR.glob(ext):
        f.unlink()
        removed += 1
if removed:
    print(f"  Removidos {removed} arquivo(s) antigos")

print(f"\nGerando arquivos para {today.strftime('%d/%m/%Y')} em {DEMO_DIR}\n")

# ─── 1. BACKLOG OSA — 80 remessas ─────────────────────────────────────────────
# 0-7:  ATA crítica  →  Status SAP "ATA Aprovado"    →  novo
# 8-25: ATA alta     →  Status SAP "Empenho Liberado" → novo
# 26-41: novos normais → novo
# 42-57: em trânsito  → status interno em_transito
# 58-68: entregues    → status interno entregue
# 69-74: em rota      → status interno em_rota_entrega
# 75-77: coletados    → status interno coletado
# 78-79: tentativa    → status interno tentativa
_SAP_HIST = {range(42, 58): "Em Transito", range(58, 69): "Entregue",
             range(69, 75): "Em Rota",     range(75, 78): "Coletado",
             range(78, 80): "Tentativa"}

rows = []
for i in range(80):
    num = 4000001 + i
    is_critica = i < 8
    is_ata     = 8 <= i < 26
    if i % 5 == 4:
        cli, cid = INT_SP[i % len(INT_SP)]
    else:
        cli, cid = CAP_SP[i % len(CAP_SP)]
    row = sap_row(num, cli, cid, is_ata=is_ata, is_critica=is_critica)
    for rng, status_val in _SAP_HIST.items():
        if i in rng:
            row["Status"] = status_val
            break
    rows.append(row)

save(rows, f"Backlog_SAP_OSA_{DATA_STR}.xlsx")

# ─── 2. BACKLOG ITJ — 40 remessas ─────────────────────────────────────────────
# 0-4:  ATA crítica  →  SLA "ATA - 24h URGENTE"  →  novo
# 5-14: ATA alta     →  SLA "ATA - 48h"           →  novo
# 15-22: novos normais → novo
# 23-30: em trânsito  → status interno em_transito
# 31-35: entregues    → status interno entregue
# 36-38: em rota      → status interno em_rota_entrega
# 39:    tentativa    → status interno tentativa
_UPS_HIST = {range(23, 31): "Em Transito", range(31, 36): "Entregue",
             range(36, 39): "Em Rota",     range(39, 40): "Tentativa"}

rows = []
for i in range(40):
    num = 0x4B000001 + i
    is_critica = i < 5
    is_ata     = 5 <= i < 15
    cli, _ = SC_CLI[i % len(SC_CLI)]
    row = ups_row(num, cli, is_ata=is_ata, is_critica=is_critica)
    for rng, sla_val in _UPS_HIST.items():
        if i in rng:
            row["Prazo SLA"] = sla_val
            break
    rows.append(row)

save(rows, f"Backlog_UPS_WMS_ITJ_{DATA_STR}.xlsx")

# ─── 3. DEMO OSA 1 — mix regular (30 remessas) ────────────────────────────────
# 1 crítica | 6 alta | 23 normais — para mostrar pipeline ao vivo
rows = []
for i in range(30):
    num = 5000001 + i
    is_critica = i == 0
    is_ata     = 1 <= i < 7
    cli, cid = CAP_SP[i % len(CAP_SP)]
    rows.append(sap_row(num, cli, cid, is_ata=is_ata, is_critica=is_critica))

save(rows, f"Remessas_SAP_OSA_{DATA_STR}.xlsx")

# ─── 4. DEMO OSA 2 — foco ATA/urgência (25 remessas) ─────────────────────────
# 5 críticas | 15 alta | 5 normais — para destacar gestão de prioridades
rows = []
for i in range(25):
    num = 5000031 + i
    is_critica = i < 5
    is_ata     = 5 <= i < 20
    all_cli = CAP_SP + INT_SP
    cli, cid = all_cli[i % len(all_cli)]
    rows.append(sap_row(num, cli, cid, is_ata=is_ata, is_critica=is_critica))

save(rows, f"Remessas_SAP_OSA_ATA_{DATA_STR}.xlsx")

# ─── 5. DEMO ITJ 1 — mix regular (20 remessas) ───────────────────────────────
rows = []
for i in range(20):
    num = 0x5B000001 + i
    is_ata = i < 4
    cli, _ = SC_CLI[i % len(SC_CLI)]
    rows.append(ups_row(num, cli, is_ata=is_ata))

save(rows, f"UPS_WMS_Export_ITJ_{DATA_STR}.xlsx")

# ─── 6. DEMO ITJ 2 — foco ATA (20 remessas) ──────────────────────────────────
rows = []
for i in range(20):
    num = 0x5B000015 + i
    is_critica = i < 5
    is_ata     = 5 <= i < 15
    cli, _ = SC_CLI[i % len(SC_CLI)]
    rows.append(ups_row(num, cli, is_ata=is_ata, is_critica=is_critica))

save(rows, f"UPS_WMS_Export_ITJ_ATA_{DATA_STR}.xlsx")

print(f"\nPronto! 6 arquivos em data/demo/")
print(f"\n  BACKLOG (subir primeiro para montar o quadro geral):")
print(f"    Backlog_SAP_OSA_{DATA_STR}.xlsx         - CD Osasco  | 80 remessas")
print(f"    Backlog_UPS_WMS_ITJ_{DATA_STR}.xlsx     - CD Itajai  | 40 remessas")
print(f"\n  APRESENTACAO (subir ao vivo durante a demo):")
print(f"    Remessas_SAP_OSA_{DATA_STR}.xlsx        - OSA mix    | 30 remessas")
print(f"    Remessas_SAP_OSA_ATA_{DATA_STR}.xlsx    - OSA ATA    | 25 remessas")
print(f"    UPS_WMS_Export_ITJ_{DATA_STR}.xlsx      - ITJ mix    | 20 remessas")
print(f"    UPS_WMS_Export_ITJ_ATA_{DATA_STR}.xlsx  - ITJ ATA    | 20 remessas")

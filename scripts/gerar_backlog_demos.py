"""
Gera os arquivos de BACKLOG para demonstração do PEO-BD, substituindo os
2 arquivos de backlog atuais (Backlog_SAP_OSA / Backlog_UPS_WMS_ITJ) por
3 arquivos por CD:
  • 2 arquivos limpos — processam sem nenhum erro de ingestão
  • 1 arquivo com erro — algumas linhas sem número de remessa/ID_UPS,
    para exercitar o tratamento de erro do Agente Ingestor (visível no
    Painel de Diagnóstico e no card "Último Erro Identificado")

Gerados:
  Backlog_SAP_OSA_1_DDMMYYYY.xlsx       — 40 remessas, sem erros
  Backlog_SAP_OSA_2_DDMMYYYY.xlsx       — 40 remessas, sem erros
  Backlog_SAP_OSA_ERRO_DDMMYYYY.xlsx    — 20 remessas, 5 com erro
  Backlog_UPS_WMS_ITJ_1_DDMMYYYY.xlsx    — 20 remessas, sem erros
  Backlog_UPS_WMS_ITJ_2_DDMMYYYY.xlsx    — 20 remessas, sem erros
  Backlog_UPS_WMS_ITJ_ERRO_DDMMYYYY.xlsx — 10 remessas, 3 com erro

Execute: python scripts/gerar_backlog_demos.py
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

random.seed(2026)

today    = date.today()
DATA_STR = today.strftime("%d%m%Y")
DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
DEMO_DIR.mkdir(parents=True, exist_ok=True)

# ─── Clientes ──────────────────────────────────────────────────────────────────

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

# Janelas de entrega — valores ≤ 2h de intervalo → janela crítica → prioridade "alta"
JANELAS = [
    "08h-12h", "08h-12h", "13h-17h", "13h-17h",
    "09h-11h", "07h-09h", "14h-16h",
    "Qualquer", "Qualquer", "Qualquer",
]

SERVICOS_UPS = ["EXPRESS", "EXPRESS", "STANDARD", "STANDARD", "PRIORITY"]

# Marcador que sobrevive à leitura do Excel como texto não-vazio (" ".strip() == "")
# mas não colide com a lista de NA padrão do pandas (que trata "" e "nan" como NaN).
CAMPO_EM_BRANCO = " "


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
        "Remessa":       str(num),
        "Cliente":       cliente,
        "Cidade":        cidade,
        "Vol (m³)":      _vol(),
        "Peso (kg)":     _peso(),
        "Valor NF":      _valor(),
        "Janela":        janela,
        "Status":        status,
        "Num.Empenho":   empenho,
        "Prazo.Empenho": prazo,
        "NF":            f"{num:06d}",
        "Qtd.Volumes":   _qtd(),
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


def aplicar_historico(rows: list, mapa: dict, campo: str):
    """Sobrescreve `campo` (Status ou Prazo SLA) nas linhas cujo índice cai
    num dos ranges de `mapa`, simulando remessas em estágios variados do
    ciclo de vida (não apenas 'novo')."""
    for i, row in enumerate(rows):
        for rng, valor in mapa.items():
            if i in rng:
                row[campo] = valor
                break


def save(rows: list, name: str):
    df   = pd.DataFrame(rows)
    path = DEMO_DIR / name
    df.to_excel(path, index=False, engine="openpyxl")
    print(f"  OK {name}  ({len(rows)} remessas)")
    return path


# ─── Remove apenas os arquivos de backlog antigos ────────────────────────────
removidos = 0
for f in DEMO_DIR.glob("Backlog_*"):
    f.unlink()
    removidos += 1
if removidos:
    print(f"  Removidos {removidos} arquivo(s) de backlog antigos")

print(f"\nGerando arquivos de backlog para {today.strftime('%d/%m/%Y')} em {DEMO_DIR}\n")

# ══════════════════════════════════════════════════════════════════════════
#  CD OSA — SAP — 2 arquivos limpos + 1 com erro
# ══════════════════════════════════════════════════════════════════════════

_SAP_HIST_1 = {range(20, 28): "Em Transito", range(28, 34): "Entregue",
               range(34, 38): "Em Rota",     range(38, 40): "Coletado"}

rows = []
for i in range(40):
    num        = 4000001 + i
    is_critica = i < 4
    is_ata     = 4 <= i < 12
    cli, cid   = INT_SP[i % len(INT_SP)] if i % 5 == 4 else CAP_SP[i % len(CAP_SP)]
    rows.append(sap_row(num, cli, cid, is_ata=is_ata, is_critica=is_critica))
aplicar_historico(rows, _SAP_HIST_1, "Status")
save(rows, f"Backlog_SAP_OSA_1_{DATA_STR}.xlsx")

_SAP_HIST_2 = {range(20, 27): "Em Transito", range(27, 33): "Entregue",
               range(33, 37): "Em Rota",     range(37, 40): "Tentativa"}

rows = []
for i in range(40):
    num        = 4000041 + i
    is_critica = i < 4
    is_ata     = 4 <= i < 12
    cli, cid   = INT_SP[i % len(INT_SP)] if i % 5 == 4 else CAP_SP[i % len(CAP_SP)]
    rows.append(sap_row(num, cli, cid, is_ata=is_ata, is_critica=is_critica))
aplicar_historico(rows, _SAP_HIST_2, "Status")
save(rows, f"Backlog_SAP_OSA_2_{DATA_STR}.xlsx")

# Arquivo com erro: 20 remessas, 5 delas sem número de remessa preenchido —
# o Agente Ingestor rejeita cada uma dessas linhas com "numero_remessa vazio"
# (registrado no histórico como erro_sistema, visível no Painel de Diagnóstico).
LINHAS_QUEBRADAS_OSA = {2, 6, 10, 14, 18}

rows = []
for i in range(20):
    num        = 4000081 + i
    is_critica = i < 2
    is_ata     = 2 <= i < 6
    cli, cid   = CAP_SP[i % len(CAP_SP)]
    row = sap_row(num, cli, cid, is_ata=is_ata, is_critica=is_critica)
    if i in LINHAS_QUEBRADAS_OSA:
        row["Remessa"] = CAMPO_EM_BRANCO
    rows.append(row)
save(rows, f"Backlog_SAP_OSA_ERRO_{DATA_STR}.xlsx")

# ══════════════════════════════════════════════════════════════════════════
#  CD ITJ — UPS_WMS — 2 arquivos limpos + 1 com erro
# ══════════════════════════════════════════════════════════════════════════

_UPS_HIST_1 = {range(11, 15): "Em Transito", range(15, 18): "Entregue",
               range(18, 20): "Em Rota"}

rows = []
for i in range(20):
    num        = 0x4B000001 + i
    is_critica = i < 2
    is_ata     = 2 <= i < 7
    cli, _     = SC_CLI[i % len(SC_CLI)]
    rows.append(ups_row(num, cli, is_ata=is_ata, is_critica=is_critica))
aplicar_historico(rows, _UPS_HIST_1, "Prazo SLA")
save(rows, f"Backlog_UPS_WMS_ITJ_1_{DATA_STR}.xlsx")

_UPS_HIST_2 = {range(11, 14): "Em Transito", range(14, 17): "Entregue",
               range(17, 20): "Tentativa"}

rows = []
for i in range(20):
    num        = 0x4B000021 + i
    is_critica = i < 2
    is_ata     = 2 <= i < 7
    cli, _     = SC_CLI[i % len(SC_CLI)]
    rows.append(ups_row(num, cli, is_ata=is_ata, is_critica=is_critica))
aplicar_historico(rows, _UPS_HIST_2, "Prazo SLA")
save(rows, f"Backlog_UPS_WMS_ITJ_2_{DATA_STR}.xlsx")

# Arquivo com erro: 10 remessas, 3 delas sem ID_UPS preenchido.
LINHAS_QUEBRADAS_ITJ = {1, 4, 7}

rows = []
for i in range(10):
    num        = 0x4B000041 + i
    is_critica = i < 1
    is_ata     = 1 <= i < 3
    cli, _     = SC_CLI[i % len(SC_CLI)]
    row = ups_row(num, cli, is_ata=is_ata, is_critica=is_critica)
    if i in LINHAS_QUEBRADAS_ITJ:
        row["ID_UPS"] = CAMPO_EM_BRANCO
    rows.append(row)
save(rows, f"Backlog_UPS_WMS_ITJ_ERRO_{DATA_STR}.xlsx")

print(f"\nPronto! 6 arquivos de backlog em data/demo/")
print(f"\n  CD OSA (SAP):")
print(f"    Backlog_SAP_OSA_1_{DATA_STR}.xlsx     - 40 remessas, sem erros")
print(f"    Backlog_SAP_OSA_2_{DATA_STR}.xlsx     - 40 remessas, sem erros")
print(f"    Backlog_SAP_OSA_ERRO_{DATA_STR}.xlsx  - 20 remessas, 5 com erro (Remessa em branco)")
print(f"\n  CD ITJ (UPS_WMS):")
print(f"    Backlog_UPS_WMS_ITJ_1_{DATA_STR}.xlsx     - 20 remessas, sem erros")
print(f"    Backlog_UPS_WMS_ITJ_2_{DATA_STR}.xlsx     - 20 remessas, sem erros")
print(f"    Backlog_UPS_WMS_ITJ_ERRO_{DATA_STR}.xlsx  - 10 remessas, 3 com erro (ID_UPS em branco)")

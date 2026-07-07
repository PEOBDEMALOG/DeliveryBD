#!/usr/bin/env python3
"""
Seed de demonstração — PEO-BD Becton Dickinson
Popula o banco com dados históricos realistas e gera os arquivos de upload demo.

Uso:
    python scripts/seed_demo.py
"""

import asyncio
import hashlib
import sys
from datetime import date, datetime, timedelta, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from core.config import settings, IS_VERCEL, DB_CONNECT_ARGS
from core.models import (
    Base, CentroDistribuicao, Transportadora, Veiculo,
    Cliente, Remessa, Upload, Alerta, EventoRastreio,
    PlanoDia, Onda, OndaRemessa, ProgramacaoColeta,
    OportunidadeConsolidacao, TabelaPrecoTransportadora,
)

HOJE      = date.today()
ONTEM     = HOJE - timedelta(days=1)
ANTEONTEM = HOJE - timedelta(days=2)


# ── Clientes ──────────────────────────────────────────────────────────────────

CLIENTES = [
    # Capital SP — hospitais sem armazenagem
    dict(codigo_sap="C001", razao_social="Hospital das Clínicas FMUSP",       tipo="hospital",    cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=False, janela_inicio=time(8,0),  janela_fim=time(12,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=1.5),
    dict(codigo_sap="C002", razao_social="Hospital Oswaldo Cruz",              tipo="hospital",    cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=False, janela_inicio=time(9,0),  janela_fim=time(12,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=1.2),
    dict(codigo_sap="C003", razao_social="Hospital Santa Catarina SP",         tipo="hospital",    cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=False, janela_inicio=time(8,0),  janela_fim=time(10,0), janela_flexivel=False, contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=0.8),
    dict(codigo_sap="C004", razao_social="UPA Lapa",                           tipo="hospital",    cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=False, janela_inicio=time(7,0),  janela_fim=time(9,0),  janela_flexivel=False, contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=0.4),
    # Capital SP — hospitais com armazenagem
    dict(codigo_sap="C005", razao_social="Hospital Albert Einstein",           tipo="hospital",    cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=time(10,0), janela_fim=time(16,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=45, volume_medio_m3=3.0),
    dict(codigo_sap="C006", razao_social="Hospital Sírio-Libanês",             tipo="hospital",    cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=time(9,0),  janela_fim=time(17,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=45, volume_medio_m3=2.8),
    # Capital SP — laboratórios
    dict(codigo_sap="C007", razao_social="Fleury Medicina Diagnóstica SP",     tipo="laboratorio", cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=2.5),
    dict(codigo_sap="C008", razao_social="Delboni Auriemo",                    tipo="laboratorio", cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=2.2),
    dict(codigo_sap="C009", razao_social="Hermes Pardini SP",                  tipo="laboratorio", cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=3.5),
    dict(codigo_sap="C010", razao_social="Laboratório Lavoisier SP",           tipo="laboratorio", cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=1.8),
    # Capital SP — universidade
    dict(codigo_sap="C011", razao_social="UNIFESP Ciências da Saúde",          tipo="universidade",cidade="São Paulo",      uf="SP", regiao="capital_sp",  tem_armazenagem=True,  janela_inicio=time(8,0),  janela_fim=time(17,0), janela_flexivel=True,  contrato_ata=True,  prazo_ata_dias=60, volume_medio_m3=4.0),
    # Interior SP
    dict(codigo_sap="C012", razao_social="Hospital Municipal de Campinas",     tipo="hospital",    cidade="Campinas",       uf="SP", regiao="interior_sp", tem_armazenagem=False, janela_inicio=time(9,0),  janela_fim=time(13,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=1.0),
    dict(codigo_sap="C013", razao_social="Hospital das Clínicas UNICAMP",      tipo="hospital",    cidade="Campinas",       uf="SP", regiao="interior_sp", tem_armazenagem=True,  janela_inicio=time(8,0),  janela_fim=time(16,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=2.5),
    dict(codigo_sap="C014", razao_social="Hermes Pardini Campinas",            tipo="laboratorio", cidade="Campinas",       uf="SP", regiao="interior_sp", tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=3.0),
    dict(codigo_sap="C015", razao_social="Hospital Estadual Ribeirão Preto",   tipo="hospital",    cidade="Ribeirão Preto", uf="SP", regiao="interior_sp", tem_armazenagem=False, janela_inicio=time(10,0), janela_fim=time(14,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=0.8),
    dict(codigo_sap="C016", razao_social="Laboratório Lavoisier Campinas",     tipo="laboratorio", cidade="Campinas",       uf="SP", regiao="interior_sp", tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=2.2),
    # Sul — SC
    dict(codigo_ups="U001", razao_social="Hospital Regional de Itajaí",        tipo="hospital",    cidade="Itajaí",         uf="SC", regiao="sul",         tem_armazenagem=False, janela_inicio=time(9,0),  janela_fim=time(12,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=1.5),
    dict(codigo_ups="U002", razao_social="Hospital Municipal de Blumenau",     tipo="hospital",    cidade="Blumenau",       uf="SC", regiao="sul",         tem_armazenagem=False, janela_inicio=time(8,0),  janela_fim=time(12,0), janela_flexivel=False, contrato_ata=True,  prazo_ata_dias=30, volume_medio_m3=1.2),
    dict(codigo_ups="U003", razao_social="Hospital São José Joinville",        tipo="hospital",    cidade="Joinville",      uf="SC", regiao="sul",         tem_armazenagem=True,  janela_inicio=time(10,0), janela_fim=time(16,0), janela_flexivel=False, contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=2.0),
    dict(codigo_ups="U004", razao_social="Laboratório Advance Florianópolis",  tipo="laboratorio", cidade="Florianópolis",  uf="SC", regiao="sul",         tem_armazenagem=True,  janela_inicio=None,       janela_fim=None,       janela_flexivel=True,  contrato_ata=False, prazo_ata_dias=None, volume_medio_m3=2.8),
    dict(codigo_ups="U005", razao_social="UFSC Hospital Universitário",        tipo="universidade",cidade="Florianópolis",  uf="SC", regiao="sul",         tem_armazenagem=True,  janela_inicio=time(8,0),  janela_fim=time(17,0), janela_flexivel=True,  contrato_ata=True,  prazo_ata_dias=60, volume_medio_m3=3.5),
]


# ── Remessas históricas (já no banco antes do demo) ───────────────────────────

# Ontem: batch já em andamento
HISTORICO_ONTEM = [
    # em_transito DHL
    dict(numero="8100001", cd="OSA", cliente_sap="C001", vol=1.4, peso=82,  nf="NF-11201", valor=14200, status="em_transito",    transp="DHL"),
    dict(numero="8100002", cd="OSA", cliente_sap="C005", vol=2.8, peso=145, nf="NF-11202", valor=42000, status="em_transito",    transp="DHL"),
    dict(numero="8100003", cd="OSA", cliente_sap="C007", vol=2.5, peso=130, nf="NF-11203", valor=31000, status="em_rota_entrega", transp="DHL"),
    dict(numero="8100004", cd="OSA", cliente_sap="C009", vol=3.2, peso=170, nf="NF-11204", valor=28500, status="em_rota_entrega", transp="DHL"),
    dict(numero="8100005", cd="OSA", cliente_sap="C012", vol=0.9, peso=55,  nf="NF-11205", valor=9800,  status="em_transito",    transp="DHL"),
    dict(numero="8100006", cd="OSA", cliente_sap="C013", vol=2.3, peso=120, nf="NF-11206", valor=22000, status="em_transito",    transp="DHL"),
    # tentativas (geram problema de OTIF)
    dict(numero="8100007", cd="OSA", cliente_sap="C003", vol=0.7, peso=40,  nf="NF-11207", valor=5600,  status="tentativa",      transp="DHL"),
    dict(numero="8100008", cd="OSA", cliente_sap="C004", vol=0.4, peso=22,  nf="NF-11208", valor=3200,  status="tentativa",      transp="DHL"),
    # ITJ — UPS
    dict(numero="U-55001", cd="ITJ", cliente_ups="U001", vol=1.4, peso=85,  nf="NF-22101", valor=16500, status="em_rota_entrega", transp="UPS"),
    dict(numero="U-55002", cd="ITJ", cliente_ups="U002", vol=1.1, peso=65,  nf="NF-22102", valor=11800, status="em_transito",    transp="UPS"),
    dict(numero="U-55003", cd="ITJ", cliente_ups="U004", vol=2.6, peso=135, nf="NF-22103", valor=29000, status="em_transito",    transp="UPS"),
]

# Anteontem: já concluídas (para OTIF)
HISTORICO_ANTEONTEM_ENTREGUE = [
    dict(numero="8090001", cd="OSA", cliente_sap="C001", vol=1.2, peso=70,  nf="NF-10001", valor=13500, status="entregue", transp="DHL"),
    dict(numero="8090002", cd="OSA", cliente_sap="C002", vol=1.0, peso=58,  nf="NF-10002", valor=9800,  status="entregue", transp="DHL"),
    dict(numero="8090003", cd="OSA", cliente_sap="C005", vol=3.1, peso=160, nf="NF-10003", valor=47000, status="entregue", transp="DHL"),
    dict(numero="8090004", cd="OSA", cliente_sap="C006", vol=2.7, peso=140, nf="NF-10004", valor=38000, status="entregue", transp="DHL"),
    dict(numero="8090005", cd="OSA", cliente_sap="C007", vol=2.4, peso=125, nf="NF-10005", valor=29000, status="entregue", transp="DHL"),
    dict(numero="8090006", cd="OSA", cliente_sap="C008", vol=2.0, peso=105, nf="NF-10006", valor=24500, status="entregue", transp="DHL"),
    dict(numero="8090007", cd="OSA", cliente_sap="C009", vol=3.3, peso=175, nf="NF-10007", valor=31000, status="entregue", transp="DHL"),
    dict(numero="8090008", cd="OSA", cliente_sap="C011", vol=3.8, peso=195, nf="NF-10008", valor=52000, status="entregue", transp="DHL"),
    dict(numero="8090009", cd="OSA", cliente_sap="C012", vol=0.8, peso=48,  nf="NF-10009", valor=8700,  status="entregue", transp="DHL"),
    dict(numero="8090010", cd="OSA", cliente_sap="C013", vol=2.2, peso=115, nf="NF-10010", valor=21000, status="entregue", transp="DHL"),
    dict(numero="8090011", cd="OSA", cliente_sap="C014", vol=2.9, peso=150, nf="NF-10011", valor=27500, status="entregue", transp="DHL"),
    dict(numero="8090012", cd="OSA", cliente_sap="C015", vol=0.7, peso=42,  nf="NF-10012", valor=7200,  status="entregue", transp="DHL"),
    dict(numero="U-54001", cd="ITJ", cliente_ups="U001", vol=1.3, peso=78,  nf="NF-20001", valor=15200, status="entregue", transp="UPS"),
    dict(numero="U-54002", cd="ITJ", cliente_ups="U002", vol=1.0, peso=60,  nf="NF-20002", valor=10800, status="entregue", transp="UPS"),
    dict(numero="U-54003", cd="ITJ", cliente_ups="U003", vol=1.9, peso=95,  nf="NF-20003", valor=23000, status="entregue", transp="UPS"),
    dict(numero="U-54004", cd="ITJ", cliente_ups="U004", vol=2.7, peso=140, nf="NF-20004", valor=30000, status="entregue", transp="UPS"),
    dict(numero="U-54005", cd="ITJ", cliente_ups="U005", vol=3.4, peso=175, nf="NF-20005", valor=48000, status="entregue", transp="UPS"),
    # devolvido
    dict(numero="U-54006", cd="ITJ", cliente_ups="U002", vol=1.1, peso=62,  nf="NF-20006", valor=11500, status="devolvido", transp="UPS"),
]


async def seed():
    print("=" * 60)
    print("PEO-BD — Seed de Demonstração")
    print("=" * 60)

    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    demo_dir = ROOT / "data" / "demo"
    if not IS_VERCEL:
        # No Vercel, /var/task é somente leitura — nem o diretório de sqlite
        # nem os arquivos de demo (só úteis para reupload local) fazem sentido lá.
        (ROOT / "data" / "db").mkdir(parents=True, exist_ok=True)
        demo_dir.mkdir(parents=True, exist_ok=True)

    engine_kwargs: dict = {"echo": False}
    if "postgresql" in settings.DATABASE_URL:
        engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

    print("  Recriando banco de dados...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSession() as db:

        # ── CDs ───────────────────────────────────────────────────────────────
        cd_osa = CentroDistribuicao(codigo="OSA", nome="CD Osasco",  cidade="Osasco",  uf="SP", sistema_origem="SAP",     capacidade_dia=10000)
        cd_itj = CentroDistribuicao(codigo="ITJ", nome="CD Itajaí",  cidade="Itajaí",  uf="SC", sistema_origem="UPS_WMS", capacidade_dia=5000)
        db.add_all([cd_osa, cd_itj])
        await db.flush()

        # ── Transportadoras ───────────────────────────────────────────────────
        t_dhl    = Transportadora(codigo="DHL",      nome="DHL Express Brasil",   email_operacoes="operacoes.sp@dhl.com",    cd_id=cd_osa.id, integracao="email",   sla_resposta_h=2)
        t_ups    = Transportadora(codigo="UPS",      nome="UPS Brasil",           email_operacoes="coletas.sc@ups.com",      cd_id=cd_itj.id, integracao="email",   sla_resposta_h=2)
        t_frota  = Transportadora(codigo="FROTA_BD", nome="Frota Própria BD SP",  email_operacoes=None,                      cd_id=cd_osa.id, integracao="interno", sla_resposta_h=0)
        t_jadlog = Transportadora(codigo="JADLOG",   nome="Jadlog Logística",     email_operacoes="coletas.sp@jadlog.com.br", cd_id=cd_osa.id, integracao="email",  sla_resposta_h=4)
        t_tnt    = Transportadora(codigo="TNT",      nome="TNT Mercúrio (FedEx)", email_operacoes="operacoes@tnt.com.br",    cd_id=cd_osa.id, integracao="email",   sla_resposta_h=3)
        db.add_all([t_dhl, t_ups, t_frota, t_jadlog, t_tnt])
        await db.flush()

        # ── Tabelas de Preço das Transportadoras ─────────────────────────────
        # Helper: mapeia código de região para colunas do modelo
        def _p(tid, regiao, **kw):
            _RM = {
                "capital_sp":  dict(macro_regiao="Sudeste", estado="São Paulo",      uf="SP", classificacao="Capital"),
                "interior_sp": dict(macro_regiao="Sudeste", estado="São Paulo",      uf="SP", classificacao="Interior"),
                "sul":         dict(macro_regiao="Sul",     estado="Santa Catarina", uf="SC", classificacao="Interior"),
            }
            return TabelaPrecoTransportadora(transportadora_id=tid, **_RM[regiao], **kw)

        # DHL Express Brasil — premium, rápido
        precos_dhl = [
            _p(t_dhl.id, "capital_sp",  preco_por_kg=12.00, preco_minimo=180.00, prazo_frac_dias=1),
            _p(t_dhl.id, "capital_sp",  preco_ftl_fixo=4200.00, prazo_ftl_dias=1),
            _p(t_dhl.id, "interior_sp", preco_por_kg=9.00,  preco_minimo=250.00, prazo_frac_dias=2),
            _p(t_dhl.id, "interior_sp", preco_ftl_fixo=6500.00, prazo_ftl_dias=2),
            _p(t_dhl.id, "sul",         preco_por_kg=15.00, preco_minimo=350.00, prazo_frac_dias=3),
            _p(t_dhl.id, "sul",         preco_ftl_fixo=8800.00, prazo_ftl_dias=3),
        ]
        # Jadlog — custo-benefício, cobertura nacional
        precos_jadlog = [
            _p(t_jadlog.id, "capital_sp",  preco_por_kg=8.00,  preco_minimo=120.00, prazo_frac_dias=2),
            _p(t_jadlog.id, "capital_sp",  preco_ftl_fixo=3200.00, prazo_ftl_dias=2),
            _p(t_jadlog.id, "interior_sp", preco_por_kg=7.00,  preco_minimo=200.00, prazo_frac_dias=3),
            _p(t_jadlog.id, "interior_sp", preco_ftl_fixo=5200.00, prazo_ftl_dias=3),
            _p(t_jadlog.id, "sul",         preco_por_kg=11.00, preco_minimo=280.00, prazo_frac_dias=4),
            _p(t_jadlog.id, "sul",         preco_ftl_fixo=7200.00, prazo_ftl_dias=4),
        ]
        # TNT Mercúrio — bom para FTL, prazo intermediário
        precos_tnt = [
            _p(t_tnt.id, "capital_sp",  preco_por_kg=10.00, preco_minimo=150.00, prazo_frac_dias=1),
            _p(t_tnt.id, "capital_sp",  preco_ftl_fixo=3800.00, prazo_ftl_dias=1),
            _p(t_tnt.id, "interior_sp", preco_por_kg=8.00,  preco_minimo=220.00, prazo_frac_dias=2),
            _p(t_tnt.id, "interior_sp", preco_ftl_fixo=5800.00, prazo_ftl_dias=2),
            _p(t_tnt.id, "sul",         preco_por_kg=13.00, preco_minimo=300.00, prazo_frac_dias=3),
            _p(t_tnt.id, "sul",         preco_ftl_fixo=8000.00, prazo_ftl_dias=3),
        ]
        db.add_all(precos_dhl + precos_jadlog + precos_tnt)
        await db.flush()

        # ── Veículos ──────────────────────────────────────────────────────────
        veiculos = [
            Veiculo(cd_id=cd_osa.id, tipo="truck",         placa="ABC-1234", proprietario="dhl",          capacidade_m3=30.0, capacidade_kg=10000.0),
            Veiculo(cd_id=cd_osa.id, tipo="vuc_eletrico",  placa="ELT-0001", proprietario="frota_propria", capacidade_m3=20.0, capacidade_kg=3500.0),
            Veiculo(cd_id=cd_osa.id, tipo="vuc_combustao", placa="VUC-5678", proprietario="frota_propria", capacidade_m3=10.0, capacidade_kg=2000.0),
            Veiculo(cd_id=cd_osa.id, tipo="van",           placa="VAN-0001", proprietario="frota_propria", capacidade_m3=3.0,  capacidade_kg=600.0),
            Veiculo(cd_id=cd_osa.id, tipo="van",           placa="VAN-0002", proprietario="frota_propria", capacidade_m3=3.0,  capacidade_kg=600.0),
            Veiculo(cd_id=cd_osa.id, tipo="van",           placa="VAN-0003", proprietario="frota_propria", capacidade_m3=3.0,  capacidade_kg=600.0),
            Veiculo(cd_id=cd_itj.id, tipo="truck",         placa="UPS-9900", proprietario="ups",           capacidade_m3=30.0, capacidade_kg=10000.0),
        ]
        db.add_all(veiculos)
        await db.flush()

        # ── Clientes ──────────────────────────────────────────────────────────
        cliente_map_sap = {}
        cliente_map_ups = {}

        for c in CLIENTES:
            obj = Cliente(
                codigo_sap      = c.get("codigo_sap"),
                codigo_ups      = c.get("codigo_ups"),
                razao_social    = c["razao_social"],
                tipo            = c["tipo"],
                cidade          = c["cidade"],
                uf              = c["uf"],
                regiao          = c["regiao"],
                tem_armazenagem = c["tem_armazenagem"],
                janela_inicio   = c.get("janela_inicio"),
                janela_fim      = c.get("janela_fim"),
                janela_flexivel = c["janela_flexivel"],
                contrato_ata    = c["contrato_ata"],
                prazo_ata_dias  = c.get("prazo_ata_dias"),
                volume_medio_m3 = c["volume_medio_m3"],
                perfil_volume   = "fracionado",
            )
            db.add(obj)
            await db.flush()
            if c.get("codigo_sap"):
                cliente_map_sap[c["codigo_sap"]] = obj
            if c.get("codigo_ups"):
                cliente_map_ups[c["codigo_ups"]] = obj

        cd_map = {"OSA": cd_osa, "ITJ": cd_itj}
        t_map  = {"DHL": t_dhl, "UPS": t_ups, "FROTA_BD": t_frota}

        # ── Remessas históricas — anteontem (entregues/devolvidas) ─────────────
        for r in HISTORICO_ANTEONTEM_ENTREGUE:
            cd      = cd_map[r["cd"]]
            cliente = cliente_map_sap.get(r.get("cliente_sap")) or cliente_map_ups.get(r.get("cliente_ups"))
            rm = Remessa(
                numero_remessa = r["numero"],
                origem         = "SAP" if r["cd"] == "OSA" else "UPS_WMS",
                cd_id          = cd.id,
                cliente_id     = cliente.id if cliente else None,
                data_extracao  = ANTEONTEM,
                volume_m3      = r["vol"],
                peso_kg        = r["peso"],
                valor_nf       = r["valor"],
                nf_emitida     = True,
                numero_nf      = r["nf"],
                status         = r["status"],
                prioridade     = "normal",
                hash_remessa   = hashlib.sha256(r["numero"].encode()).hexdigest(),
            )
            db.add(rm)
            await db.flush()

            ev = EventoRastreio(
                remessa_id      = rm.id,
                transportadora  = r["transp"],
                codigo_rastreio = r["numero"],
                status          = r["status"],
                localizacao     = cliente.cidade if cliente else "SP",
                evento_em       = datetime.combine(ANTEONTEM, time(15, 30)),
                capturado_em    = datetime.combine(ANTEONTEM, time(15, 30)),
                fonte           = "portal_" + r["transp"].lower(),
            )
            db.add(ev)

        # ── Remessas históricas — ontem (em andamento) ─────────────────────────
        for r in HISTORICO_ONTEM:
            cd      = cd_map[r["cd"]]
            cliente = cliente_map_sap.get(r.get("cliente_sap")) or cliente_map_ups.get(r.get("cliente_ups"))
            rm = Remessa(
                numero_remessa = r["numero"],
                origem         = "SAP" if r["cd"] == "OSA" else "UPS_WMS",
                cd_id          = cd.id,
                cliente_id     = cliente.id if cliente else None,
                data_extracao  = ONTEM,
                volume_m3      = r["vol"],
                peso_kg        = r["peso"],
                valor_nf       = r["valor"],
                nf_emitida     = True,
                numero_nf      = r["nf"],
                status         = r["status"],
                prioridade     = "alta" if r["status"] == "tentativa" else "normal",
                hash_remessa   = hashlib.sha256(r["numero"].encode()).hexdigest(),
            )
            db.add(rm)
            await db.flush()

            ev = EventoRastreio(
                remessa_id      = rm.id,
                transportadora  = r["transp"],
                codigo_rastreio = r["numero"],
                status          = r["status"],
                localizacao     = cliente.cidade if cliente else "SP",
                evento_em       = datetime.combine(ONTEM, time(9, 15)),
                capturado_em    = datetime.combine(ONTEM, time(9, 15)),
                fonte           = "portal_" + r["transp"].lower(),
            )
            db.add(ev)

            # Alertas para tentativas
            if r["status"] == "tentativa":
                db.add(Alerta(
                    tipo       = "tentativa_entrega",
                    severidade = "alta",
                    titulo     = f"Tentativa de entrega falhou — {r['numero']}",
                    descricao  = (
                        f"Transportadora {r['transp']} registrou tentativa sem sucesso. "
                        f"Cliente {cliente.razao_social if cliente else 'N/D'} — acione e reagende."
                    ),
                    remessa_id = rm.id,
                    cliente_id = cliente.id if cliente else None,
                    cd_id      = cd.id,
                ))

        # ── Alerta crítico pré-existente ──────────────────────────────────────
        db.add(Alerta(
            tipo       = "backlog_critico",
            severidade = "critica",
            titulo     = "Backlog crítico — 20.000 volumes em aberto",
            descricao  = (
                "Volume acumulado supera capacidade diária dos CDs (OSA: 10.000/dia, ITJ: 5.000/dia). "
                "Priorize remessas ATA e janelas críticas na montagem do plano de hoje."
            ),
            cd_id      = cd_osa.id,
        ))

        # ── Oportunidades de consolidação FTL ─────────────────────────────────
        db.add(OportunidadeConsolidacao(
            cd_id             = cd_osa.id,
            regiao            = "interior_sp",
            data_analise      = HOJE,
            qtd_clientes      = 4,
            volume_atual_m3   = 16.2,
            tipo_atual        = "fracionado",
            tipo_possivel     = "ftl",
            economia_estimada = 4850.00,
            acao_sugerida     = (
                "Consolidar Hermes Pardini Campinas, Lavoisier Campinas, HC UNICAMP e "
                "Hospital Estadual Ribeirão Preto em carga FTL única. Volume agregado: 16.2 m³ "
                "(acima do limiar de 15 m³). Economia estimada de frete: R$ 4.850,00/dia."
            ),
            status            = "aberta",
        ))
        db.add(OportunidadeConsolidacao(
            cd_id             = cd_osa.id,
            regiao            = "capital_sp",
            data_analise      = HOJE,
            qtd_clientes      = 5,
            volume_atual_m3   = 12.0,
            tipo_atual        = "fracionado",
            tipo_possivel     = "ftl",
            economia_estimada = 3200.00,
            acao_sugerida     = (
                "Fleury, Delboni, Hermes Pardini SP, Lavoisier SP e UNIFESP recebem entregas "
                "fracionadas semanalmente. Volume médio conjunto: 12 m³. Com 3 dias de janela "
                "(Ter/Qui/Sex) atingem 15 m³ — viável converter para FTL. Economia: R$ 3.200,00/semana."
            ),
            status            = "aberta",
        ))

        # ── Backlog 30 dias ───────────────────────────────────────────────────
        POOL_SAP = ["C001","C002","C003","C005","C006","C007","C008","C009","C010","C011","C012","C013","C014","C015","C016"]
        vals_nf  = [4800, 9200, 14500, 22000, 31000, 38000, 47000, 52000, 8700, 17500, 21000, 27500, 29000, 31000, 42000]
        vols_m3  = [0.4,  0.8,  1.2,  1.5,  2.0,  2.2,  2.5,  2.8,  0.6,  1.0,  1.8,  2.3,  3.0,  3.4,  3.9]
        pesos_kg = [24,   48,   72,   88,   110,  125,  138,  155,  36,   60,   105,  128,  158,  175,  200]

        num_back = 7800000
        for dias_atras in range(30, 0, -1):
            data_extrac = HOJE - timedelta(days=dias_atras)
            qtd = 3 if dias_atras > 20 else 5 if dias_atras > 7 else 2
            for i in range(qtd):
                idx     = (dias_atras * 7 + i) % len(POOL_SAP)
                cod     = POOL_SAP[idx]
                cliente = cliente_map_sap.get(cod)

                if dias_atras > 7:
                    st = "entregue"
                    nf_ok = True
                elif dias_atras > 3:
                    st = "em_transito" if i % 3 != 0 else "tentativa"
                    nf_ok = True
                else:
                    st = "novo"
                    nf_ok = (i % 4 != 0)   # 25% sem NF no backlog recente

                numero  = str(num_back + dias_atras * 10 + i)
                is_ata  = (idx % 5 == 0)
                prazo   = (HOJE + timedelta(days=max(1, (idx % 20) + 1))) if is_ata else None

                rm = Remessa(
                    numero_remessa = numero,
                    origem         = "SAP",
                    cd_id          = cd_osa.id,
                    cliente_id     = cliente.id if cliente else None,
                    data_extracao  = data_extrac,
                    volume_m3      = vols_m3[idx],
                    peso_kg        = pesos_kg[idx],
                    valor_nf       = vals_nf[idx],
                    nf_emitida     = nf_ok,
                    numero_nf      = f"NF-BK{numero}" if nf_ok else None,
                    status         = st,
                    prioridade     = "critica" if (is_ata and prazo and (prazo - HOJE).days <= 5) else ("alta" if is_ata else "normal"),
                    is_ata         = is_ata,
                    prazo_empenho  = prazo,
                    hash_remessa   = hashlib.sha256(f"BK{numero}".encode()).hexdigest(),
                )
                db.add(rm)

        await db.commit()
        print("  Banco populado com dados históricos.")

    await engine.dispose()

    # ── Gera arquivos de demo ──────────────────────────────────────────────────
    # Só faz sentido localmente — no Vercel /var/task é somente leitura e não há
    # como servir esses arquivos de qualquer forma.
    if not IS_VERCEL:
        _gerar_sap_xlsx(demo_dir)
        _gerar_ups_csv(demo_dir)
        _gerar_sap_xlsx_backlog(demo_dir)

    print("=" * 60)
    print("Seed concluído!")
    if not IS_VERCEL:
        print(f"  Demo SAP : data/demo/Remessas_SAP_OSA_{HOJE.strftime('%d%m%Y')}.xlsx")
        print(f"  Demo UPS : data/demo/UPS_WMS_Export_ITJ_{HOJE.strftime('%d%m%Y')}.csv")
    print("  Inicie o servidor: uvicorn api.main:app --reload")
    print("  Acesse: http://localhost:8000")
    print("=" * 60)


def _gerar_sap_xlsx(demo_dir: Path):
    """Gera planilha SAP fictícia — Osasco — para upload demo."""
    hoje_str    = HOJE.strftime("%d/%m/%Y")
    d2          = (HOJE + timedelta(days=2)).strftime("%d/%m/%Y")   # prazo crítico
    d4          = (HOJE + timedelta(days=4)).strftime("%d/%m/%Y")   # prazo alto
    d10         = (HOJE + timedelta(days=10)).strftime("%d/%m/%Y")  # prazo normal

    rows = [
        # ATA crítico — prazo 2 dias
        ["8000101", "Hospital das Clínicas FMUSP",     "São Paulo",      0.85, 52,  14500, "08h-12h", "ATA - Crítico", "EMP-2026-0156", d2,  "NF-12801",   2],
        ["8000102", "Hospital Oswaldo Cruz",            "São Paulo",      1.20, 74,   9200, "09h-12h", "ATA - Crítico", "EMP-2026-0157", d2,  "NF-12802",   3],
        # ATA prazo 4 dias — NF pendente (alerta crítico)
        ["8000103", "Hospital Santa Catarina SP",       "São Paulo",      0.50, 30,   4200, "08h-10h", "ATA - Empenho", "EMP-2026-0158", d4,  "Pendente SAP", 1],
        # ATA prazo 4 dias
        ["8000104", "Hospital Albert Einstein",         "São Paulo",      2.50, 120, 47000, "10h-16h", "ATA - Empenho", "EMP-2026-0159", d4,  "NF-12804",   6],
        # ATA prazo 10 dias
        ["8000105", "Hospital Sírio-Libanês",           "São Paulo",      2.80, 140, 39000, "09h-17h", "ATA - Empenho", "EMP-2026-0160", d10, "NF-12805",   7],
        ["8000106", "UNIFESP Ciências da Saúde",        "São Paulo",      3.90, 195, 54000, "08h-17h", "ATA - Empenho", "EMP-2026-0161", d10, "NF-12806",   9],
        # Janela crítica 2h — sem ATA
        ["8000107", "UPA Lapa",                         "São Paulo",      0.35, 20,   2800, "07h-09h", "Normal",        "",              "",   "NF-12807",   1],
        # Normal capital SP
        ["8000108", "Fleury Medicina Diagnóstica SP",   "São Paulo",      2.40, 125, 30000, "Qualquer","Normal",        "",              "",   "NF-12808",   6],
        ["8000109", "Fleury Medicina Diagnóstica SP",   "São Paulo",      2.50, 130, 31500, "Qualquer","Normal",        "",              "",   "NF-12809",   6],
        ["8000110", "Delboni Auriemo",                  "São Paulo",      2.20, 115, 25500, "Qualquer","Normal",        "",              "",   "NF-12810",   5],
        ["8000111", "Delboni Auriemo",                  "São Paulo",      2.10, 110, 24000, "Qualquer","Normal",        "",              "",   "NF-12811",   5],
        ["8000112", "Hermes Pardini SP",                "São Paulo",      3.40, 175, 29000, "Qualquer","Normal",        "",              "",   "NF-12812",   8],
        ["8000113", "Hermes Pardini SP",                "São Paulo",      3.50, 180, 30500, "Qualquer","Normal",        "",              "",   "NF-12813",   8],
        ["8000114", "Laboratório Lavoisier SP",         "São Paulo",      1.80, 95,  19500, "Qualquer","Normal",        "",              "",   "NF-12814",   4],
        ["8000115", "Laboratório Lavoisier SP",         "São Paulo",      1.75, 90,  18800, "Qualquer","Normal",        "",              "",   "NF-12815",   4],
        # Interior SP — oportunidade FTL
        ["8000116", "Hospital Municipal de Campinas",   "Campinas",       1.00, 60,  10200, "09h-13h", "ATA - Empenho", "EMP-2026-0162", d10, "NF-12816",   3],
        ["8000117", "Hospital das Clínicas UNICAMP",    "Campinas",       2.50, 130, 24000, "08h-16h", "ATA - Empenho", "EMP-2026-0163", d10, "NF-12817",   6],
        ["8000118", "Hermes Pardini Campinas",          "Campinas",       3.00, 155, 28500, "Qualquer","Normal",        "",              "",   "NF-12818",   7],
        ["8000119", "Hermes Pardini Campinas",          "Campinas",       2.90, 150, 27000, "Qualquer","Normal",        "",              "",   "NF-12819",   7],
        ["8000120", "Laboratório Lavoisier Campinas",   "Campinas",       2.20, 115, 22000, "Qualquer","Normal",        "",              "",   "NF-12820",   5],
        ["8000121", "Laboratório Lavoisier Campinas",   "Campinas",       2.10, 110, 20500, "Qualquer","Normal",        "",              "",   "NF-12821",   5],
        ["8000122", "Hospital Estadual Ribeirão Preto", "Ribeirão Preto", 0.80, 48,   7800, "10h-14h", "ATA - Empenho", "EMP-2026-0164", d4,  "NF-12822",   2],
        ["8000123", "Hospital Estadual Ribeirão Preto", "Ribeirão Preto", 0.75, 44,   7200, "10h-14h", "Normal",        "",              "",   "NF-12823",   2],
        # NF pendente extra
        ["8000124", "Fleury Medicina Diagnóstica SP",   "São Paulo",      2.45, 128, 29500, "Qualquer","Normal",        "",              "",   "Pendente SAP", 6],
        ["8000125", "Hospital das Clínicas UNICAMP",    "Campinas",       2.55, 133, 23500, "08h-16h", "Normal",        "",              "",   "Pendente SAP", 6],
        # Frota própria (capital SP pequenos volumes)
        ["8000126", "UPA Lapa",                         "São Paulo",      0.30, 18,   2400, "07h-09h", "Normal",        "",              "",   "NF-12826",   1],
        ["8000127", "Hospital Santa Catarina SP",       "São Paulo",      0.45, 27,   3900, "08h-10h", "Normal",        "",              "",   "NF-12827",   1],
        ["8000128", "Hospital das Clínicas FMUSP",      "São Paulo",      0.80, 48,  13200, "08h-12h", "Normal",        "",              "",   "NF-12828",   2],
        ["8000129", "Delboni Auriemo",                  "São Paulo",      2.30, 120, 26000, "Qualquer","Normal",        "",              "",   "NF-12829",   5],
        ["8000130", "Laboratório Lavoisier SP",         "São Paulo",      1.65, 85,  17500, "Qualquer","Normal",        "",              "",   "NF-12830",   4],
        ["8000131", "Hospital Albert Einstein",         "São Paulo",      2.90, 150, 43000, "10h-16h", "Normal",        "",              "",   "NF-12831",   7],
        ["8000132", "Hospital Sírio-Libanês",           "São Paulo",      2.70, 138, 37500, "09h-17h", "Normal",        "",              "",   "NF-12832",   7],
        ["8000133", "UNIFESP Ciências da Saúde",        "São Paulo",      4.10, 210, 57000, "08h-17h", "Normal",        "",              "",   "NF-12833",   10],
        ["8000134", "Hermes Pardini SP",                "São Paulo",      3.20, 165, 28000, "Qualquer","Normal",        "",              "",   "NF-12834",   8],
        ["8000135", "Hospital Municipal de Campinas",   "Campinas",       0.95, 57,   9600, "09h-13h", "Normal",        "",              "",   "NF-12835",   3],
    ]

    colunas = ["Remessa","Cliente","Cidade","Vol (m³)","Peso (kg)","Valor NF",
               "Janela","Status","Num.Empenho","Prazo.Empenho","NF","Qtd.Volumes"]
    df = pd.DataFrame(rows, columns=colunas)

    nome = f"Remessas_SAP_OSA_{HOJE.strftime('%d%m%Y')}.xlsx"
    path = demo_dir / nome
    df.to_excel(path, index=False, engine="openpyxl")
    print(f"  Gerado: {path}")


def _gerar_ups_csv(demo_dir: Path):
    """Gera export UPS WMS fictício — Itajaí — para upload demo."""
    d3  = (HOJE + timedelta(days=3)).strftime("%d/%m/%Y")
    d8  = (HOJE + timedelta(days=8)).strftime("%d/%m/%Y")
    d15 = (HOJE + timedelta(days=15)).strftime("%d/%m/%Y")

    rows = [
        ["U-66001", "Hospital Regional de Itajaí",       "SC", "EXPRESS", 88,  3, "NF-30201", f"ATA D+{3}  {d3}"],
        ["U-66002", "Hospital Regional de Itajaí",       "SC", "EXPRESS", 82,  2, "NF-30202", f"ATA D+{3}  {d3}"],
        ["U-66003", "Hospital Municipal de Blumenau",    "SC", "EXPRESS", 65,  2, "NF-30203", f"ATA D+{3}  {d3}"],
        ["U-66004", "Hospital Municipal de Blumenau",    "SC", "EXPRESS", 72,  2, "NF-30204", "Normal"],
        ["U-66005", "Hospital São José Joinville",       "SC", "STANDARD",102, 3, "NF-30205", "Normal"],
        ["U-66006", "Hospital São José Joinville",       "SC", "STANDARD",98,  3, "NF-30206", "Normal"],
        ["U-66007", "Laboratório Advance Florianópolis", "SC", "STANDARD",138, 4, "NF-30207", "Normal"],
        ["U-66008", "Laboratório Advance Florianópolis", "SC", "STANDARD",145, 4, "NF-30208", "Normal"],
        ["U-66009", "UFSC Hospital Universitário",       "SC", "STANDARD",178, 5, "NF-30209", f"ATA D+{8}  {d8}"],
        ["U-66010", "UFSC Hospital Universitário",       "SC", "STANDARD",182, 5, "NF-30210", f"ATA D+{8}  {d8}"],
        ["U-66011", "Hospital Regional de Itajaí",       "SC", "EXPRESS", 75,  2, "Pendente", "Normal"],
        ["U-66012", "Hospital Municipal de Blumenau",    "SC", "EXPRESS", 68,  2, "NF-30212", "Normal"],
        ["U-66013", "Laboratório Advance Florianópolis", "SC", "STANDARD",135, 4, "NF-30213", f"ATA D+{15} {d15}"],
        ["U-66014", "Hospital São José Joinville",       "SC", "STANDARD",95,  3, "NF-30214", "Normal"],
        ["U-66015", "UFSC Hospital Universitário",       "SC", "STANDARD",170, 5, "NF-30215", "Normal"],
        ["U-66016", "Laboratório Advance Florianópolis", "SC", "STANDARD",142, 4, "NF-30216", "Normal"],
        ["U-66017", "Hospital Regional de Itajaí",       "SC", "EXPRESS", 80,  2, "NF-30217", f"ATA D+{3}  {d3}"],
        ["U-66018", "Hospital Municipal de Blumenau",    "SC", "EXPRESS", 63,  2, "NF-30218", "Normal"],
    ]

    colunas = ["ID_UPS","Destinatário","UF","Serviço","Peso","Volumes","NF","Prazo SLA"]
    df = pd.DataFrame(rows, columns=colunas)

    nome = f"UPS_WMS_Export_ITJ_{HOJE.strftime('%d%m%Y')}.csv"
    path = demo_dir / nome
    df.to_csv(path, index=False, encoding="utf-8")
    print(f"  Gerado: {path}")


def _gerar_sap_xlsx_backlog(demo_dir: Path):
    """Gera planilha SAP com backlog acumulado de 30 dias — para teste de carga."""
    d1  = (HOJE + timedelta(days=1)).strftime("%d/%m/%Y")
    d3  = (HOJE + timedelta(days=3)).strftime("%d/%m/%Y")
    d7  = (HOJE + timedelta(days=7)).strftime("%d/%m/%Y")
    d15 = (HOJE + timedelta(days=15)).strftime("%d/%m/%Y")
    d30 = (HOJE + timedelta(days=30)).strftime("%d/%m/%Y")

    rows = [
        # ATAs com prazo crítico (1-3 dias) — geram alertas críticos
        ["BK-9001", "Hospital das Clínicas FMUSP",     "São Paulo",      1.20, 72,  16800, "08h-12h", "ATA - Crítico", "EMP-BK-001", d1,  "Pendente SAP",  3],
        ["BK-9002", "Hospital Oswaldo Cruz",            "São Paulo",      0.95, 56,  10200, "09h-12h", "ATA - Crítico", "EMP-BK-002", d1,  "NF-BK-002",     2],
        ["BK-9003", "UPA Lapa",                         "São Paulo",      0.30, 18,   2600, "07h-09h", "ATA - Crítico", "EMP-BK-003", d3,  "Pendente SAP",  1],
        ["BK-9004", "Hospital Santa Catarina SP",       "São Paulo",      0.45, 27,   3800, "08h-10h", "ATA - Empenho", "EMP-BK-004", d3,  "NF-BK-004",     1],
        # Duplicata de remessa já existente (8000101)
        ["8000101", "Hospital das Clínicas FMUSP",     "São Paulo",      0.85, 52,  14500, "08h-12h", "ATA - Crítico", "EMP-2026-0156", d1, "NF-12801",     2],
        # ATAs prazo 7 dias
        ["BK-9005", "Hospital Albert Einstein",         "São Paulo",      3.20, 165, 51000, "10h-16h", "ATA - Empenho", "EMP-BK-005", d7,  "NF-BK-005",     8],
        ["BK-9006", "Hospital Sírio-Libanês",           "São Paulo",      2.90, 148, 41000, "09h-17h", "ATA - Empenho", "EMP-BK-006", d7,  "NF-BK-006",     7],
        ["BK-9007", "UNIFESP Ciências da Saúde",        "São Paulo",      4.20, 212, 56000, "08h-17h", "ATA - Empenho", "EMP-BK-007", d7,  "NF-BK-007",    10],
        ["BK-9008", "Hospital Municipal de Campinas",   "Campinas",       1.05, 63,  11000, "09h-13h", "ATA - Empenho", "EMP-BK-008", d7,  "Pendente SAP",  3],
        # Remessas normais acumuladas — sem NF
        ["BK-9009", "Fleury Medicina Diagnóstica SP",  "São Paulo",      2.40, 125, 30000, "Qualquer","Normal",        "",            "",   "Pendente SAP",  6],
        ["BK-9010", "Delboni Auriemo",                  "São Paulo",      2.20, 115, 25500, "Qualquer","Normal",        "",            "",   "Pendente SAP",  5],
        ["BK-9011", "Hermes Pardini SP",                "São Paulo",      3.40, 175, 29000, "Qualquer","Normal",        "",            "",   "Pendente SAP",  8],
        # Remessas normais (NF ok) capital SP
        ["BK-9012", "Fleury Medicina Diagnóstica SP",  "São Paulo",      2.50, 130, 31500, "Qualquer","Normal",        "",            "",   "NF-BK-012",     6],
        ["BK-9013", "Delboni Auriemo",                  "São Paulo",      2.10, 110, 24000, "Qualquer","Normal",        "",            "",   "NF-BK-013",     5],
        ["BK-9014", "Hermes Pardini SP",                "São Paulo",      3.50, 180, 30500, "Qualquer","Normal",        "",            "",   "NF-BK-014",     8],
        ["BK-9015", "Laboratório Lavoisier SP",         "São Paulo",      1.80, 95,  19500, "Qualquer","Normal",        "",            "",   "NF-BK-015",     4],
        ["BK-9016", "Laboratório Lavoisier SP",         "São Paulo",      1.75, 90,  18800, "Qualquer","Normal",        "",            "",   "NF-BK-016",     4],
        # Interior SP — oportunidade FTL (vol total > 15m³)
        ["BK-9017", "Hospital das Clínicas UNICAMP",   "Campinas",       2.80, 142, 26500, "08h-16h", "ATA - Empenho", "EMP-BK-009", d15, "NF-BK-017",     7],
        ["BK-9018", "Hermes Pardini Campinas",          "Campinas",       3.10, 158, 29500, "Qualquer","Normal",        "",            "",   "NF-BK-018",     7],
        ["BK-9019", "Hermes Pardini Campinas",          "Campinas",       2.90, 152, 27000, "Qualquer","Normal",        "",            "",   "NF-BK-019",     7],
        ["BK-9020", "Laboratório Lavoisier Campinas",   "Campinas",       2.20, 115, 22000, "Qualquer","Normal",        "",            "",   "NF-BK-020",     5],
        ["BK-9021", "Laboratório Lavoisier Campinas",   "Campinas",       2.10, 110, 20500, "Qualquer","Normal",        "",            "",   "NF-BK-021",     5],
        ["BK-9022", "Hospital Estadual Ribeirão Preto", "Ribeirão Preto", 0.80, 48,   7800, "10h-14h", "ATA - Empenho","EMP-BK-010", d30, "NF-BK-022",     2],
        # ATAs prazo 30 dias
        ["BK-9023", "Hospital das Clínicas FMUSP",     "São Paulo",      0.90, 54,  15200, "08h-12h", "ATA - Empenho", "EMP-BK-011", d30, "NF-BK-023",     2],
        ["BK-9024", "Hospital Oswaldo Cruz",            "São Paulo",      1.10, 66,   9500, "09h-12h", "ATA - Empenho", "EMP-BK-012", d30, "NF-BK-024",     3],
        ["BK-9025", "Hospital Albert Einstein",         "São Paulo",      2.60, 132, 43500, "10h-16h", "Normal",        "",            "",   "NF-BK-025",     6],
    ]

    colunas = ["Remessa","Cliente","Cidade","Vol (m³)","Peso (kg)","Valor NF",
               "Janela","Status","Num.Empenho","Prazo.Empenho","NF","Qtd.Volumes"]
    df = pd.DataFrame(rows, columns=colunas)

    nome = f"Remessas_SAP_OSA_BACKLOG_{HOJE.strftime('%d%m%Y')}.xlsx"
    path = demo_dir / nome
    df.to_excel(path, index=False, engine="openpyxl")
    print(f"  Gerado (backlog): {path}")


if __name__ == "__main__":
    asyncio.run(seed())

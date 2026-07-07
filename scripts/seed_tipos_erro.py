#!/usr/bin/env python3
"""
Seed do catálogo de tipos de erro — PEO-BD
Insere os 14 tipos canônicos em tipos_erro (upsert por código).

Uso:
    python scripts/seed_tipos_erro.py
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from core.config import settings
from core.models import TipoErro

_TIPOS = [
    {
        "codigo": "TIMEOUT_SAP",
        "descricao": "Timeout na integração com o SAP ao buscar backlog de remessas",
        "gravidade": "alerta",
        "acao_sugerida": "Verificar conectividade com o servidor SAP e reprocessar o upload após 5 minutos.",
    },
    {
        "codigo": "TIMEOUT_UPS",
        "descricao": "Timeout ao consultar a API do WMS UPS para confirmação de coleta",
        "gravidade": "alerta",
        "acao_sugerida": "Checar status da API UPS no painel de integrações e repetir a consulta.",
    },
    {
        "codigo": "ARQUIVO_CORROMPIDO",
        "descricao": "Arquivo de upload não pode ser lido — formato inválido ou dados binários corrompidos",
        "gravidade": "critico",
        "acao_sugerida": "Solicitar reenvio do arquivo ao time SAP/TI. Não reprocessar o arquivo corrompido.",
    },
    {
        "codigo": "COLUNA_AUSENTE",
        "descricao": "Coluna obrigatória ausente no arquivo de upload (ex.: numero_remessa, volume_m3)",
        "gravidade": "critico",
        "acao_sugerida": "Confirmar com TI o template correto do arquivo e reenviar com todas as colunas obrigatórias.",
    },
    {
        "codigo": "NF_DUPLICADA",
        "descricao": "Nota fiscal já presente no banco — remessa ignorada para evitar duplicidade",
        "gravidade": "info",
        "acao_sugerida": "Verificar se a NF foi enviada em upload anterior. Nenhuma ação necessária se já processada.",
    },
    {
        "codigo": "CLIENTE_NAO_RECONHECIDO",
        "descricao": "CNPJ do cliente não encontrado no cadastro — remessa não pôde ser classificada",
        "gravidade": "alerta",
        "acao_sugerida": "Cadastrar o cliente no sistema ou corrigir o CNPJ no arquivo de origem (SAP).",
    },
    {
        "codigo": "REGIAO_INVALIDA",
        "descricao": "UF/região do destinatário não mapeada nas tabelas de preço das transportadoras ativas",
        "gravidade": "alerta",
        "acao_sugerida": "Adicionar a região na tabela de preços da transportadora responsável pelo atendimento.",
    },
    {
        "codigo": "FALHA_API_TRANSPORTADORA",
        "descricao": "Erro HTTP ao consultar ou enviar dados para a API da transportadora",
        "gravidade": "alerta",
        "acao_sugerida": "Verificar credenciais da API e status do serviço da transportadora. Reenviar após resolução.",
    },
    {
        "codigo": "CD_INDISPONIVEL",
        "descricao": "Centro de distribuição marcado como inativo ou sem capacidade disponível para o dia",
        "gravidade": "critico",
        "acao_sugerida": "Reativar o CD no painel de configurações ou redistribuir as remessas para outro CD.",
    },
    {
        "codigo": "VALOR_FORA_FAIXA",
        "descricao": "Valor monetário ou peso da remessa fora dos limites aceitos pelo sistema (negativo ou excessivo)",
        "gravidade": "alerta",
        "acao_sugerida": "Corrigir o valor no SAP e reenviar o arquivo. Verificar se há erro de unidade (g vs kg).",
    },
    {
        "codigo": "ATA_VENCIDA",
        "descricao": "Remessa com prazo de empenho (ATA) já vencido no momento do processamento",
        "gravidade": "critico",
        "acao_sugerida": "Acionar imediatamente o time de expedição. Verificar possibilidade de entrega emergencial.",
    },
    {
        "codigo": "HASH_DUPLICADO",
        "descricao": "Hash do arquivo de upload idêntico a um já processado — arquivo ignorado",
        "gravidade": "info",
        "acao_sugerida": "Confirmar se o arquivo foi enviado duas vezes. Nenhuma ação necessária se for duplicata intencional.",
    },
    {
        "codigo": "FALHA_PDF",
        "descricao": "Erro ao gerar o PDF do plano do dia ou da programação de coleta",
        "gravidade": "alerta",
        "acao_sugerida": "Tentar gerar o PDF novamente pelo painel. Se persistir, verificar logs do serviço ReportLab.",
    },
    {
        "codigo": "BANCO_INDISPONIVEL",
        "descricao": "Falha de conexão com o banco de dados durante operação crítica",
        "gravidade": "critico",
        "acao_sugerida": "Verificar status do banco de dados e conexão de rede. Sistema entrará em modo de espera.",
    },
]


async def seed():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as db:
        inseridos = 0
        atualizados = 0

        for dados in _TIPOS:
            res = await db.execute(
                select(TipoErro).where(TipoErro.codigo == dados["codigo"])
            )
            existente = res.scalar_one_or_none()

            if existente:
                existente.descricao     = dados["descricao"]
                existente.gravidade     = dados["gravidade"]
                existente.acao_sugerida = dados["acao_sugerida"]
                atualizados += 1
            else:
                db.add(TipoErro(**dados))
                inseridos += 1

        await db.commit()

    await engine.dispose()

    print(f"\n✓ Catálogo de erros sincronizado: {inseridos} inseridos, {atualizados} atualizados\n")
    print(f"{'Código':<30} {'Gravidade':<10} Descrição")
    print("-" * 80)
    for t in _TIPOS:
        print(f"{t['codigo']:<30} {t['gravidade']:<10} {t['descricao'][:45]}")
    print()


if __name__ == "__main__":
    asyncio.run(seed())

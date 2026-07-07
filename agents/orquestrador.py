# peo_bd/agents/orquestrador.py
# Orquestrador Central
# Responsabilidade: coordena os 5 agentes em sequência e expõe
# o método único que Timóteo/Carlos acionam ao fazer upload do arquivo.

import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from agents.agente_ingestor      import AgenteIngestor
from agents.agente_classificador import AgenteClassificador
from agents.agente_montador      import AgenteMontador
from agents.agente_comunicador   import AgenteComunicador
from agents.agente_monitor       import AgenteMonitor
from agents.agente_resolvedor    import AgenteResolvedor

logger = logging.getLogger(__name__)


class Orquestrador:
    """
    Orquestrador Central — Pipeline completo de expedição outbound BD.

    Sequência:
      [Upload CSV/XLSX]
          → Agente 1 — Ingestor       (normaliza, deduplica, persiste remessas)
          → Agente 2 — Classificador  (alertas: ATA, janela, NF, consolidação)
          → Agente 3 — Montador       (monta ondas, dimensiona carga)
          → Agente 4 — Comunicador    (gera xlsx por transportadora, envia e-mail)
          → Agente 5 — Monitor        (inicia monitoramento de rastreio)

    Cada agente pode ser chamado individualmente ou em pipeline completo.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.ingestor      = AgenteIngestor(db)
        self.classificador = AgenteClassificador(db)
        self.montador      = AgenteMontador(db)
        self.comunicador   = AgenteComunicador(db, dry_run=settings.SMTP_USER == "")
        self.monitor       = AgenteMonitor(db)
        self.resolvedor    = AgenteResolvedor(db)

    # ── Pipeline completo ──────────────────────────────────────────────────────

    async def processar_arquivo(
        self,
        arquivo_path: Path,
        cd_codigo: str,
        usuario: str,
        origem: str | None = None,
        data_plano: date | None = None,
        ciclo: int = 1,
        auto_enviar: bool = True,
    ) -> dict[str, Any]:
        """
        Fluxo completo a partir de um upload de arquivo.
        Retorna relatório consolidado de todos os agentes.

        Parâmetros:
          arquivo_path  — caminho do arquivo CSV/XLSX
          cd_codigo     — 'OSA' ou 'ITJ'
          usuario       — 'timoteo' ou 'carlos'
          origem        — 'SAP' | 'UPS_WMS' | None (auto-detect)
          data_plano    — data do planejamento (default: hoje)
          ciclo         — 1=manhã, 2=tarde (fechamento de mês)
          auto_enviar   — False = monta o plano mas não envia e-mails
        """

        resultado: dict[str, Any] = {
            "arquivo": arquivo_path.name,
            "cd":      cd_codigo,
            "usuario": usuario,
            "etapas":  {},
        }

        # ── Etapa 1: Ingestão ──────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info(f"[Orquestrador] ETAPA 1 — Ingestão: {arquivo_path.name}")
        try:
            rel_ingestao = await self.ingestor.processar_upload(
                arquivo_path, cd_codigo, usuario, origem
            )
            resultado["etapas"]["ingestao"] = rel_ingestao
            upload_id = rel_ingestao["upload_id"]
        except Exception as e:
            codigo_erro = self._mapear_codigo_erro(e, "ingestao")
            res_resolv = await self.resolvedor.tratar_erro(
                tipo_erro_codigo=codigo_erro,
                contexto={"arquivo": arquivo_path.name, "cd": cd_codigo},
                retry_callback=lambda: self.ingestor.processar_upload(
                    arquivo_path, cd_codigo, usuario, origem
                ),
            )
            if res_resolv["status"] == "resolvido":
                rel_ingestao = res_resolv["resultado_callback"]
                resultado["etapas"]["ingestao"] = rel_ingestao
                upload_id = rel_ingestao["upload_id"]
            else:
                resultado["etapas"]["ingestao"] = {"erro": str(e), "resolucao": res_resolv}
                resultado["status"] = "erro_ingestao"
                return resultado

        if rel_ingestao.get("validas", rel_ingestao.get("linhas_validas", 0)) == 0:
            resultado["status"] = "sem_remessas_validas"
            return resultado

        # ── Etapa 2: Classificação e alertas ──────────────────────────────
        logger.info(f"[Orquestrador] ETAPA 2 — Classificação (upload_id={upload_id})")
        try:
            rel_classif = await self.classificador.classificar_upload(upload_id)
            resultado["etapas"]["classificacao"] = rel_classif
        except Exception as e:
            codigo_erro = self._mapear_codigo_erro(e, "classificacao")
            res_resolv = await self.resolvedor.tratar_erro(
                tipo_erro_codigo=codigo_erro,
                contexto={"upload_id": upload_id, "cd": cd_codigo},
            )
            resultado["etapas"]["classificacao"] = {"erro": str(e), "resolucao": res_resolv}
            logger.error(f"[Orquestrador] Erro na classificação: {e} → {res_resolv['status']}")
            # Não bloqueia — continua para montagem independente da escalação

        # ── Etapa 3: Montagem das ondas ────────────────────────────────────
        logger.info(f"[Orquestrador] ETAPA 3 — Montagem de ondas ({cd_codigo})")
        try:
            rel_montagem = await self.montador.montar_plano(
                cd_codigo, data_plano, ciclo, usuario
            )
            resultado["etapas"]["montagem"] = rel_montagem
            plano_id = rel_montagem.get("plano_id")
        except Exception as e:
            codigo_erro = self._mapear_codigo_erro(e, "montagem")
            res_resolv = await self.resolvedor.tratar_erro(
                tipo_erro_codigo=codigo_erro,
                contexto={"cd": cd_codigo, "ciclo": ciclo},
                retry_callback=lambda: self.montador.montar_plano(
                    cd_codigo, data_plano, ciclo, usuario
                ),
            )
            if res_resolv["status"] == "resolvido":
                rel_montagem = res_resolv["resultado_callback"]
                resultado["etapas"]["montagem"] = rel_montagem
                plano_id = rel_montagem.get("plano_id")
            else:
                resultado["etapas"]["montagem"] = {"erro": str(e), "resolucao": res_resolv}
                resultado["status"] = "erro_montagem"
                return resultado

        if not plano_id or rel_montagem.get("ondas", 0) == 0:
            resultado["status"] = "sem_ondas_geradas"
            return resultado

        # ── Etapa 4: Programação de transportadoras ────────────────────────
        if auto_enviar:
            logger.info(f"[Orquestrador] ETAPA 4 — Programação transportadoras (plano_id={plano_id})")
            try:
                rel_envio = await self.comunicador.programar_coletas(plano_id)
                resultado["etapas"]["comunicacao"] = rel_envio
            except Exception as e:
                codigo_erro = self._mapear_codigo_erro(e, "comunicacao")
                res_resolv = await self.resolvedor.tratar_erro(
                    tipo_erro_codigo=codigo_erro,
                    contexto={"plano_id": plano_id, "cd": cd_codigo},
                    retry_callback=lambda: self.comunicador.programar_coletas(plano_id),
                )
                if res_resolv["status"] == "resolvido":
                    resultado["etapas"]["comunicacao"] = res_resolv["resultado_callback"]
                else:
                    resultado["etapas"]["comunicacao"] = {"erro": str(e), "resolucao": res_resolv}
                    logger.error(f"[Orquestrador] Erro na comunicação: {e} → {res_resolv['status']}")
        else:
            resultado["etapas"]["comunicacao"] = {"status": "pulado (auto_enviar=False)"}

        # ── Etapa 5: Ativa monitoramento ───────────────────────────────────
        logger.info("[Orquestrador] ETAPA 5 — Dashboard de rastreio atualizado")
        try:
            cd_map = {"OSA": settings.CD_OSASCO_ID, "ITJ": settings.CD_ITAJAI_ID}
            cd_id  = cd_map.get(cd_codigo)
            dashboard = await self.monitor.dashboard(cd_id)
            resultado["etapas"]["monitor"] = dashboard
        except Exception as e:
            resultado["etapas"]["monitor"] = {"erro": str(e)}

        resultado["status"] = "concluido"
        logger.info(f"[Orquestrador] Pipeline concluído: {resultado['status']}")
        return resultado

    # ── Métodos avulsos ────────────────────────────────────────────────────────

    async def dashboard_tempo_real(self, cd_codigo: str | None = None) -> dict:
        cd_map = {"OSA": settings.CD_OSASCO_ID, "ITJ": settings.CD_ITAJAI_ID}
        cd_id  = cd_map.get(cd_codigo) if cd_codigo else None
        return await self.monitor.dashboard(cd_id)

    async def verificar_sla_e_alertas(self) -> dict:
        return await self.monitor.verificar_sla()

    async def registrar_update_rastreio(
        self,
        numero_remessa: str,
        status: str,
        transportadora: str,
        localizacao: str | None = None,
        fonte: str = "manual",
    ) -> dict:
        return await self.monitor.processar_update_manual(
            numero_remessa, status, transportadora, localizacao, fonte=fonte
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _mapear_codigo_erro(self, e: Exception, etapa: str) -> str:
        """Infere o código canônico do TipoErro a partir da exceção e da etapa."""
        msg = str(e).lower()

        if "timeout" in msg:
            return "TIMEOUT_UPS" if ("ups" in msg or etapa == "comunicacao") else "TIMEOUT_SAP"
        if any(kw in msg for kw in ("corrupt", "magic bytes", "unable to read", "openpyxl", "xlrd", "zipfile")):
            return "ARQUIVO_CORROMPIDO"
        if any(kw in msg for kw in ("keyerror", "coluna", "column", "numero_remessa", "campo obrigatório")):
            return "COLUNA_AUSENTE"
        if any(kw in msg for kw in ("operationalerror", "connection", "banco", "database", "sqlite", "postgresql", "asyncpg")):
            return "BANCO_INDISPONIVEL"
        if any(kw in msg for kw in ("cd não", "cd nao", "centro de distribuição", "centro de distribuicao")):
            return "CD_INDISPONIVEL"
        if "pdf" in msg or "reportlab" in msg or "canvas" in msg:
            return "FALHA_PDF"
        if any(kw in msg for kw in ("smtp", "email", "connection refused", "mailbox")):
            return "FALHA_API_TRANSPORTADORA"

        # Fallback por etapa
        return {
            "ingestao":     "ARQUIVO_CORROMPIDO",
            "classificacao": "BANCO_INDISPONIVEL",
            "montagem":     "CD_INDISPONIVEL",
            "comunicacao":  "FALHA_API_TRANSPORTADORA",
            "monitor":      "BANCO_INDISPONIVEL",
        }.get(etapa, "BANCO_INDISPONIVEL")

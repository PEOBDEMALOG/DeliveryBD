# peo_bd/agents/agente_ingestor.py
# Agente 1 — Ingestor
# Responsabilidade: recebe arquivo CSV/Excel (SAP ou UPS_WMS),
# normaliza colunas, calcula hash de deduplicação, persiste remessas.

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.config import settings
from core.historico import HistoricoService
from core.models import (
    CentroDistribuicao, Cliente, Remessa, Upload
)

logger = logging.getLogger(__name__)

# ── Mapeamento de colunas por origem ──────────────────────────────────────────
# Cada origem tem seus próprios nomes de coluna — normalizamos aqui.

MAPA_COLUNAS_SAP = {
    "Remessa":         "numero_remessa",
    "Cliente":         "razao_social_raw",
    "Cidade":          "cidade",
    "Vol (m³)":        "volume_m3",
    "Peso (kg)":       "peso_kg",
    "Valor NF":        "valor_nf",
    "Janela":          "janela_raw",
    "Status":          "status_raw",
    "Num.Empenho":     "numero_empenho",
    "Prazo.Empenho":   "prazo_empenho",
    "NF":              "numero_nf",
    "Qtd.Volumes":     "qtd_volumes",
}

# Mapeamento de status do arquivo para status interno
MAPA_STATUS_SAP = {
    "novo":               "novo",
    "planejado":          "planejado",
    "coletado":           "coletado",
    "em transito":        "em_transito",
    "em trânsito":        "em_transito",
    "em_transito":        "em_transito",
    "em rota":            "em_rota_entrega",
    "em rota entrega":    "em_rota_entrega",
    "em_rota_entrega":    "em_rota_entrega",
    "saiu para entrega":  "em_rota_entrega",
    "entregue":           "entregue",
    "tentativa":          "tentativa",
    "tentativa de entrega": "tentativa",
    "devolvido":          "devolvido",
    "retornou":           "devolvido",
    "cancelado":          "devolvido",
}

MAPA_STATUS_UPS = {
    "novo":               "novo",
    "planejado":          "planejado",
    "coletado":           "coletado",
    "in transit":         "em_transito",
    "em transito":        "em_transito",
    "em trânsito":        "em_transito",
    "em_transito":        "em_transito",
    "out for delivery":   "em_rota_entrega",
    "em rota":            "em_rota_entrega",
    "em_rota_entrega":    "em_rota_entrega",
    "delivered":          "entregue",
    "entregue":           "entregue",
    "delivery attempt":   "tentativa",
    "tentativa":          "tentativa",
    "returned":           "devolvido",
    "devolvido":          "devolvido",
}

MAPA_COLUNAS_UPS = {
    "ID_UPS":          "numero_remessa",
    "Destinatário":    "razao_social_raw",
    "UF":              "uf",
    "Serviço":         "servico_ups",
    "Peso":            "peso_kg",
    "Volumes":         "qtd_volumes",
    "NF":              "numero_nf",
    "Prazo SLA":       "sla_raw",
    "Volume (m³)":     "volume_m3",
    "Valor NF":        "valor_nf",
    "Valor":           "valor_nf",
}

# ── Detecção de schema pelo conteúdo real do arquivo ──────────────────────────
# Colunas exclusivas de cada origem (não aparecem no mapa da outra) — usadas
# para identificar o formato real do arquivo, em vez de confiar no nome do
# arquivo ou no CD selecionado. "NF" e "Valor NF" existem nos dois mapas e por
# isso não servem como assinatura.
COLUNAS_ASSINATURA_SAP = {"Remessa", "Cliente", "Cidade", "Num.Empenho", "Prazo.Empenho"}
COLUNAS_ASSINATURA_UPS = {"ID_UPS", "Destinatário", "UF", "Serviço", "Prazo SLA"}

# Cada CD só recebe arquivos de uma origem — usado para validar a seleção
# manual do usuário contra o schema detectado no arquivo.
ORIGEM_ESPERADA_POR_CD = {"OSA": "SAP", "ITJ": "UPS_WMS"}


class AgenteIngestor:
    """
    Agente 1 — Ingestor.

    Fluxo:
      1. Detecta origem (SAP ou UPS_WMS) pelo nome do arquivo ou coluna
      2. Lê CSV/XLSX com pandas
      3. Normaliza colunas para schema interno
      4. Calcula hash SHA-256 por remessa para deduplicação
      5. Cruza com remessas já existentes no banco
      6. Persiste novas, sinaliza duplicatas
      7. Retorna relatório do upload
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.historico = HistoricoService(db)

    # ── Entrada principal ──────────────────────────────────────────────────────

    async def processar_upload(
        self,
        arquivo_path: Path,
        cd_codigo: str,           # 'OSA' ou 'ITJ'
        usuario: str,
        origem: str | None = None  # 'SAP' | 'UPS_WMS' | None (auto-detect pelo conteúdo)
    ) -> dict[str, Any]:

        logger.info(f"[Ingestor] Iniciando processamento: {arquivo_path.name}")

        # 1. Busca CD
        cd = await self._get_cd(cd_codigo)
        if not cd:
            raise ValueError(f"CD não encontrado: {cd_codigo}")

        # 2. Lê o arquivo e valida o schema ANTES de criar qualquer registro —
        #    se o conteúdo não bater com o CD selecionado, o upload é
        #    rejeitado sem deixar rastro no banco. O nome do arquivo NUNCA
        #    decide isso aqui — é usado só como sugestão de preenchimento do
        #    seletor manual no frontend.
        df = self._ler_arquivo(arquivo_path)

        schema_detectado = self._detectar_schema_arquivo(df)
        origem_esperada   = ORIGEM_ESPERADA_POR_CD.get(cd_codigo)

        if schema_detectado is None:
            raise ValueError(
                f"Não foi possível identificar o formato do arquivo '{arquivo_path.name}' — "
                f"as colunas encontradas não correspondem ao schema SAP nem ao UPS WMS. "
                f"Verifique se é uma exportação válida."
            )

        if origem_esperada and schema_detectado != origem_esperada:
            raise ValueError(
                f"Incompatibilidade entre CD selecionado e arquivo: '{arquivo_path.name}' "
                f"tem colunas do formato {schema_detectado}, mas o CD {cd_codigo} espera "
                f"arquivos no formato {origem_esperada}. Verifique o CD selecionado ou o "
                f"arquivo enviado."
            )

        if not origem:
            origem = schema_detectado
        elif origem != schema_detectado:
            raise ValueError(
                f"Origem informada ({origem}) não corresponde ao schema real do arquivo "
                f"'{arquivo_path.name}' (detectado como {schema_detectado})."
            )

        logger.info(f"[Ingestor] Schema detectado pelo conteúdo do arquivo: {schema_detectado}")

        # 3. Só agora cria o registro de upload — já validado.
        upload = Upload(
            cd_id        = cd.id,
            usuario      = usuario,
            arquivo_nome = arquivo_path.name,
            arquivo_path = str(arquivo_path),
            formato      = arquivo_path.suffix.lstrip(".").lower(),
            status       = "processando",
        )
        self.db.add(upload)
        await self.db.flush()

        upload.total_linhas = len(df)

        # 5. Normaliza colunas conforme origem
        df = self._normalizar_colunas(df, origem)

        # 6. Processa linha a linha
        validas = erros = duplicatas = 0
        log_erros = []

        for idx, row in df.iterrows():
            try:
                resultado = await self._processar_linha(
                    row, cd, upload, origem, idx + 2  # +2 = header + 1-indexed
                )
                if resultado == "duplicata":
                    duplicatas += 1
                else:
                    validas += 1
            except Exception as e:
                erros += 1
                # NaN de campos vazios do Excel não é JSON-compliant (Starlette
                # rejeita allow_nan=False na resposta) — substitui por None antes
                # de anexar ao relatório de erros.
                dados_linha = row.where(pd.notna(row), None).to_dict()
                log_erros.append({"linha": idx + 2, "erro": str(e), "dados": dados_linha})
                logger.warning(f"[Ingestor] Erro na linha {idx+2}: {e}")
                await self.historico.registrar(
                    tipo_evento="erro_sistema",
                    origem="ingestor",
                    ator_tipo="agente_ia",
                    ator_nome="Agente Ingestor",
                    cd_id=cd.id,
                    descricao=f"Erro ao processar linha {idx+2} do arquivo '{arquivo_path.name}': {e}",
                    resultado="falha",
                    gravidade="alerta",
                    dados_extra={"linha": idx + 2, "erro": str(e)},
                )

        # 7. Finaliza upload
        upload.linhas_validas = validas
        upload.linhas_erro    = erros
        upload.linhas_dup     = duplicatas
        upload.status         = "ok" if erros == 0 else "parcial"
        upload.log_erros      = json.dumps(log_erros) if log_erros else None

        await self.historico.registrar(
            tipo_evento="upload_processado",
            origem="ingestor",
            ator_tipo="agente_ia",
            ator_nome="Agente Ingestor",
            cd_id=cd.id,
            descricao=(
                f"Upload '{arquivo_path.name}' ({origem}) processado — "
                f"{validas} válidas, {duplicatas} duplicatas, {erros} erros"
            ),
            resultado="sucesso" if erros == 0 else "alerta_gerado",
            gravidade=None if erros == 0 else "alerta",
            dados_extra={
                "upload_id": upload.id,
                "arquivo": arquivo_path.name,
                "origem": origem,
                "validas": validas,
                "duplicatas": duplicatas,
                "erros": erros,
            },
        )

        await self.db.commit()

        relatorio = {
            "upload_id":      upload.id,
            "arquivo":        arquivo_path.name,
            "origem":         origem,
            "cd":             cd_codigo,
            "total":          upload.total_linhas,
            "validas":        validas,
            "duplicatas":     duplicatas,
            "erros":          erros,
            "status":         upload.status,
            "log_erros":      log_erros[:10],  # primeiros 10 erros no relatório
        }

        logger.info(f"[Ingestor] Concluído: {validas} válidas, {duplicatas} dup, {erros} erros")
        return relatorio

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _detectar_schema_arquivo(self, df: pd.DataFrame) -> str | None:
        """
        Identifica o schema real do arquivo pelas colunas presentes — nunca
        pelo nome do arquivo. Retorna None se as colunas não baterem com
        nenhum dos dois schemas conhecidos.
        """
        cols = set(df.columns)
        sap_match = len(cols & COLUNAS_ASSINATURA_SAP)
        ups_match = len(cols & COLUNAS_ASSINATURA_UPS)
        if sap_match == 0 and ups_match == 0:
            return None
        return "SAP" if sap_match >= ups_match else "UPS_WMS"

    def _ler_arquivo(self, path: Path) -> pd.DataFrame:
        ext = path.suffix.lower()
        if ext == ".csv":
            for enc in ["utf-8", "latin-1", "cp1252"]:
                try:
                    return pd.read_csv(path, sep=None, engine="python", encoding=enc)
                except UnicodeDecodeError:
                    continue
        elif ext in (".xlsx", ".xls"):
            return pd.read_excel(path, engine="openpyxl" if ext == ".xlsx" else "xlrd")
        raise ValueError(f"Formato não suportado: {ext}")

    def _normalizar_colunas(self, df: pd.DataFrame, origem: str) -> pd.DataFrame:
        mapa = MAPA_COLUNAS_SAP if origem == "SAP" else MAPA_COLUNAS_UPS
        # Renomeia apenas colunas presentes
        renomear = {k: v for k, v in mapa.items() if k in df.columns}
        df = df.rename(columns=renomear)
        # Remove linhas completamente vazias
        df = df.dropna(how="all")
        return df

    async def _processar_linha(
        self,
        row: pd.Series,
        cd: CentroDistribuicao,
        upload: Upload,
        origem: str,
        num_linha: int
    ) -> str:

        numero = str(row.get("numero_remessa", "")).strip()
        if not numero:
            raise ValueError("numero_remessa vazio")

        # Calcula hash para deduplicação
        hash_val = self._calcular_hash(row, origem)

        # Verifica se já existe no banco
        existente = await self.db.execute(
            select(Remessa).where(Remessa.hash_remessa == hash_val)
        )
        existente = existente.scalar_one_or_none()
        if existente:
            logger.debug(f"[Ingestor] Duplicata detectada: {numero}")
            return "duplicata"

        # Verifica se o número da remessa já existe no banco
        por_numero = await self.db.execute(
            select(Remessa).where(Remessa.numero_remessa == numero)
        )
        remessa_anterior = por_numero.scalar_one_or_none()
        if remessa_anterior:
            # Número já existe — qualquer variação é tratada como duplicata
            # para evitar violação de unique constraint no banco
            logger.debug(f"[Ingestor] Número já existe no banco: {numero}")
            return "duplicata"

        # Resolve cliente
        cliente = await self._resolver_cliente(row, cd, origem)

        # Parseia janela de entrega
        janela_inicio, janela_fim, janela_critica = self._parsear_janela(row, origem)

        # Detecta ATA
        is_ata, prazo_empenho, numero_empenho = self._detectar_ata(row)

        # Detecta prioridade
        prioridade = self._calcular_prioridade(
            is_ata, prazo_empenho, janela_critica
        )

        # Monta remessa
        remessa = Remessa(
            numero_remessa  = numero,
            origem          = origem,
            cd_id           = cd.id,
            cliente_id      = cliente.id if cliente else None,
            data_extracao   = date.today(),
            upload_id       = upload.id,
            volume_m3       = self._safe_float(row.get("volume_m3")),
            peso_kg         = self._safe_float(row.get("peso_kg")),
            valor_nf        = self._safe_float(row.get("valor_nf")),
            qtd_volumes     = self._safe_int(row.get("qtd_volumes")),
            status          = self._mapear_status(row, origem),
            is_ata          = is_ata,
            numero_empenho  = numero_empenho,
            prazo_empenho   = prazo_empenho,
            nf_emitida      = self._detectar_nf(row),
            numero_nf       = str(row.get("numero_nf", "") or "").strip() or None,
            janela_inicio   = janela_inicio,
            janela_fim      = janela_fim,
            janela_critica  = janela_critica,
            prioridade      = prioridade,
            hash_remessa    = hash_val,
            duplicata_de    = remessa_anterior.id if remessa_anterior else None,
        )
        # Estima volume para remessas UPS sem coluna Volume (fator 0.006 m³/kg)
        if not remessa.volume_m3 and remessa.peso_kg:
            remessa.volume_m3 = round(float(remessa.peso_kg) * 0.006, 3)
        self.db.add(remessa)
        return "ok"

    async def _resolver_cliente(
        self, row: pd.Series, cd: CentroDistribuicao, origem: str
    ) -> Cliente | None:
        razao = str(row.get("razao_social_raw", "")).strip()
        if not razao:
            return None

        # Tenta encontrar pelo nome (fuzzy simples — melhoria futura: embeddings)
        resultado = await self.db.execute(
            select(Cliente).where(Cliente.razao_social.ilike(f"%{razao[:30]}%"))
        )
        cliente = resultado.scalar_one_or_none()

        if not cliente:
            # Cria cliente básico para não bloquear o fluxo
            uf = str(row.get("uf", "")).strip().upper() or None
            cidade = str(row.get("cidade", "")).strip() or None
            cliente = Cliente(
                razao_social    = razao,
                tipo            = self._inferir_tipo_cliente(razao),
                cidade          = cidade,
                uf              = uf,
                regiao          = self._inferir_regiao(uf, cidade),
                tem_armazenagem = False,
                janela_flexivel = True,
            )
            self.db.add(cliente)
            await self.db.flush()
            logger.info(f"[Ingestor] Novo cliente criado: {razao}")

        return cliente

    def _mapear_status(self, row: pd.Series, origem: str) -> str:
        """Mapeia status do arquivo (SAP: coluna Status; UPS: coluna Prazo SLA) para status interno."""
        mapa = MAPA_STATUS_SAP if origem == "SAP" else MAPA_STATUS_UPS

        # SAP: usa campo status_raw
        status_raw = str(row.get("status_raw", "") or "").strip().lower()
        if status_raw:
            result = mapa.get(status_raw)
            if result:
                return result

        # UPS: sem coluna Status — verifica sla_raw (pode conter status de entrega)
        if origem == "UPS_WMS":
            sla_raw = str(row.get("sla_raw", "") or "").strip().lower()
            if sla_raw:
                result = MAPA_STATUS_UPS.get(sla_raw)
                if result:
                    return result

        return "novo"

    def _parsear_janela(
        self, row: pd.Series, origem: str
    ) -> tuple[Any, Any, bool]:
        """Parseia '08h-12h' → (time(8,0), time(12,0), False)"""
        janela_raw = str(row.get("janela_raw", "") or "").strip()
        sla_raw    = str(row.get("sla_raw", "") or "").strip()
        raw        = janela_raw or sla_raw

        if not raw or raw.lower() in ("qualquer", "flexível", ""):
            return None, None, False

        try:
            partes = raw.lower().replace("h", ":00").replace("–", "-").replace("—", "-")
            inicio_str, fim_str = [p.strip() for p in partes.split("-")]
            hi, mi = [int(x) for x in inicio_str.split(":")]
            hf, mf = [int(x) for x in fim_str.split(":")]
            inicio = datetime.now().replace(hour=hi, minute=mi).time()
            fim    = datetime.now().replace(hour=hf, minute=mf).time()
            critica = (hf - hi) <= settings.ALERTA_JANELA_CRITICA_H
            return inicio, fim, critica
        except Exception:
            return None, None, False

    def _detectar_ata(
        self, row: pd.Series
    ) -> tuple[bool, date | None, str | None]:
        empenho = str(row.get("numero_empenho", "") or "").strip()
        prazo_raw = row.get("prazo_empenho")
        status_raw = str(row.get("status_raw", "") or "").lower()
        sla_raw = str(row.get("sla_raw", "") or "").lower()

        is_ata = (
            bool(empenho)
            or "ata" in status_raw
            or "empenho" in status_raw
            or "ata" in sla_raw
        )

        prazo = None
        if prazo_raw and pd.notna(prazo_raw):
            try:
                prazo = pd.to_datetime(prazo_raw).date()
            except Exception:
                pass

        return is_ata, prazo, empenho or None

    def _detectar_nf(self, row: pd.Series) -> bool:
        nf = str(row.get("numero_nf", "") or "").strip().lower()
        return bool(nf) and nf not in ("pendente sap", "pendente", "n/a", "")

    def _calcular_prioridade(
        self,
        is_ata: bool,
        prazo_empenho: date | None,
        janela_critica: bool
    ) -> str:
        if is_ata and prazo_empenho:
            dias = (prazo_empenho - date.today()).days
            if dias <= settings.ALERTA_ATA_DIAS:
                return "critica"
            if dias <= 10:
                return "alta"
        if janela_critica:
            return "alta"
        if is_ata:
            return "alta"
        return "normal"

    def _calcular_hash(self, row: pd.Series, origem: str) -> str:
        """Hash SHA-256 baseado nos campos-chave da remessa."""
        campos_chave = ["numero_remessa", "peso_kg", "volume_m3", "valor_nf", "numero_nf"]
        dados = "|".join(
            str(row.get(c, "")).strip() for c in campos_chave
        )
        return hashlib.sha256(dados.encode()).hexdigest()

    def _inferir_regiao(self, uf: str | None, cidade: str | None) -> str:
        """Retorna a regiao de planejamento a partir de UF e cidade."""
        if not uf:
            return "default"
        uf = uf.upper()
        if uf == "SP":
            if cidade:
                c = cidade.lower()
                if "são paulo" in c or "sao paulo" in c:
                    return "capital_sp"
            return "interior_sp"
        regioes = {
            "SC": "sul", "RS": "sul", "PR": "sul",
            "RJ": "sudeste", "MG": "sudeste", "ES": "sudeste",
            "BA": "nordeste", "CE": "nordeste", "PE": "nordeste", "MA": "nordeste",
            "AM": "norte",  "PA": "norte",  "TO": "norte",
            "GO": "centro_oeste", "MT": "centro_oeste",
            "MS": "centro_oeste", "DF": "centro_oeste",
        }
        return regioes.get(uf, "default")

    def _inferir_tipo_cliente(self, razao: str) -> str:
        razao_lower = razao.lower()
        if any(w in razao_lower for w in ["hospital", "hosp.", "hc-", "hu-", "upa", "ubs"]):
            return "hospital"
        if any(w in razao_lower for w in ["lab", "fleury", "delboni", "hermes", "pardini"]):
            return "laboratorio"
        if any(w in razao_lower for w in ["univ", "ufsc", "usp", "unicamp", "federal"]):
            return "universidade"
        return "outros"

    async def _get_cd(self, codigo: str) -> CentroDistribuicao | None:
        res = await self.db.execute(
            select(CentroDistribuicao).where(CentroDistribuicao.codigo == codigo)
        )
        return res.scalar_one_or_none()

    # ── Utils ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_float(val) -> float | None:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        try:
            return float(str(val).replace(",", ".").replace("R$", "").replace(" ", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val) -> int | None:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None

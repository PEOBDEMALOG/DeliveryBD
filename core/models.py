# peo_bd/core/models.py
# Modelos ORM — espelham exatamente o schema.sql

from datetime import date, datetime, time
from typing import Optional, List
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey,
    Integer, JSON, Numeric, String, Text, Time, UniqueConstraint,
    event, func
)
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncAttrs


class Base(AsyncAttrs, DeclarativeBase):
    pass


class CentroDistribuicao(Base):
    __tablename__ = "centros_distribuicao"

    id              = Column(Integer, primary_key=True)
    codigo          = Column(String(10), unique=True, nullable=False)
    nome            = Column(String(100), nullable=False)
    cidade          = Column(String(100))
    uf              = Column(String(2))
    sistema_origem  = Column(String(20), nullable=False)   # 'SAP' | 'UPS_WMS'
    capacidade_dia  = Column(Integer)
    ativo           = Column(Boolean, default=True)
    criado_em       = Column(DateTime, default=datetime.utcnow)

    remessas        = relationship("Remessa",  back_populates="cd")
    veiculos        = relationship("Veiculo",  back_populates="cd")
    uploads         = relationship("Upload",   back_populates="cd")
    planos          = relationship("PlanoDia", back_populates="cd")


class Veiculo(Base):
    __tablename__ = "veiculos"

    id              = Column(Integer, primary_key=True)
    cd_id           = Column(Integer, ForeignKey("centros_distribuicao.id"))
    tipo            = Column(String(30), nullable=False)
    placa           = Column(String(10))
    proprietario    = Column(String(20), nullable=False)
    capacidade_m3   = Column(Numeric(6, 2), nullable=False)
    capacidade_kg   = Column(Numeric(8, 2), nullable=False)
    ativo           = Column(Boolean, default=True)

    cd              = relationship("CentroDistribuicao", back_populates="veiculos")
    ondas           = relationship("Onda", back_populates="veiculo")


class Transportadora(Base):
    __tablename__ = "transportadoras"

    id              = Column(Integer, primary_key=True)
    codigo          = Column(String(20), unique=True, nullable=False)
    nome            = Column(String(100), nullable=False)
    email_operacoes = Column(String(200))
    cd_id           = Column(Integer, ForeignKey("centros_distribuicao.id"))
    integracao      = Column(String(20), default="email")
    sla_resposta_h  = Column(Integer, default=2)
    ativo           = Column(Boolean, default=True)
    meta_otif       = Column(Float, default=95.0)
    # Percentual mínimo aceitável de OTIF. Default 95% (meta geral da BD).

    ondas           = relationship("Onda", back_populates="transportadora")
    programacoes    = relationship("ProgramacaoColeta", back_populates="transportadora")
    tabela_precos   = relationship("TabelaPrecoTransportadora", back_populates="transportadora")


class Cliente(Base):
    __tablename__ = "clientes"

    id              = Column(Integer, primary_key=True)
    codigo_sap      = Column(String(20), unique=True)
    codigo_ups      = Column(String(20))
    razao_social    = Column(String(200), nullable=False)
    cnpj            = Column(String(18))
    tipo            = Column(String(30), nullable=False)
    cidade          = Column(String(100))
    uf              = Column(String(2))
    cep             = Column(String(9))
    regiao          = Column(String(30))
    tem_armazenagem = Column(Boolean, default=False)
    janela_inicio   = Column(Time)
    janela_fim      = Column(Time)
    janela_flexivel = Column(Boolean, default=False)
    perfil_volume   = Column(String(20), default="fracionado")
    volume_medio_m3 = Column(Numeric(6, 2))
    contrato_ata    = Column(Boolean, default=False)
    prazo_ata_dias  = Column(Integer)
    criado_em       = Column(DateTime, default=datetime.utcnow)
    atualizado_em   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    remessas        = relationship("Remessa", back_populates="cliente")
    alertas         = relationship("Alerta",  back_populates="cliente")


class Upload(Base):
    __tablename__ = "uploads"

    id              = Column(Integer, primary_key=True)
    cd_id           = Column(Integer, ForeignKey("centros_distribuicao.id"))
    usuario         = Column(String(100))
    arquivo_nome    = Column(String(300))
    arquivo_path    = Column(String(500))
    formato         = Column(String(10))
    total_linhas    = Column(Integer)
    linhas_validas  = Column(Integer)
    linhas_erro     = Column(Integer)
    linhas_dup      = Column(Integer)
    status          = Column(String(20), default="processando")
    log_erros       = Column(Text)    # JSON serializado
    criado_em       = Column(DateTime, default=datetime.utcnow)

    cd              = relationship("CentroDistribuicao", back_populates="uploads")
    remessas        = relationship("Remessa", back_populates="upload")


class Remessa(Base):
    __tablename__ = "remessas"

    id              = Column(Integer, primary_key=True)
    numero_remessa  = Column(String(20), unique=True, nullable=False)
    origem          = Column(String(20), nullable=False)
    cd_id           = Column(Integer, ForeignKey("centros_distribuicao.id"), index=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"))
    data_extracao   = Column(Date, nullable=False, index=True)
    data_upload     = Column(DateTime, default=datetime.utcnow)
    upload_id       = Column(Integer, ForeignKey("uploads.id"))

    volume_m3       = Column(Numeric(6, 3))
    peso_kg         = Column(Numeric(8, 2))
    valor_nf        = Column(Numeric(12, 2))
    qtd_volumes     = Column(Integer)

    status          = Column(String(30), default="novo", index=True)
    tipo_entrega    = Column(String(20))
    prioridade      = Column(String(20), default="normal")

    is_ata          = Column(Boolean, default=False)
    numero_empenho  = Column(String(50))
    prazo_empenho   = Column(Date)

    nf_emitida      = Column(Boolean, default=False)
    numero_nf       = Column(String(20))
    chave_nfe       = Column(String(50))

    hash_remessa    = Column(String(64))
    duplicata_de    = Column(Integer, ForeignKey("remessas.id"))

    janela_inicio   = Column(Time)
    janela_fim      = Column(Time)
    janela_critica  = Column(Boolean, default=False)

    criado_em       = Column(DateTime, default=datetime.utcnow)
    atualizado_em   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cd              = relationship("CentroDistribuicao", back_populates="remessas")
    cliente         = relationship("Cliente",            back_populates="remessas")
    upload          = relationship("Upload",             back_populates="remessas")
    alertas         = relationship("Alerta",             back_populates="remessa")
    eventos_rastreio = relationship("EventoRastreio",   back_populates="remessa")

    @property
    def dias_restantes(self) -> Optional[int]:
        if self.prazo_empenho:
            return (self.prazo_empenho - date.today()).days
        return None


class PlanoDia(Base):
    __tablename__ = "planos_dia"

    id              = Column(Integer, primary_key=True)
    cd_id           = Column(Integer, ForeignKey("centros_distribuicao.id"))
    data_plano      = Column(Date, nullable=False)
    ciclo           = Column(Integer, default=1)
    status          = Column(String(20), default="rascunho")
    criado_por      = Column(String(100))
    aprovado_por    = Column(String(100))
    aprovado_em     = Column(DateTime)
    total_remessas  = Column(Integer)
    total_volume_m3 = Column(Numeric(8, 2))
    total_peso_kg   = Column(Numeric(10, 2))
    total_valor_nf  = Column(Numeric(14, 2))
    otif_realizado  = Column(Numeric(5, 2))
    criado_em       = Column(DateTime, default=datetime.utcnow)

    cd              = relationship("CentroDistribuicao", back_populates="planos")
    ondas           = relationship("Onda", back_populates="plano")


class Onda(Base):
    __tablename__ = "ondas"

    id                = Column(Integer, primary_key=True)
    plano_id          = Column(Integer, ForeignKey("planos_dia.id"))
    numero_onda       = Column(Integer, nullable=False)
    nome              = Column(String(100))
    regiao            = Column(String(50))
    tipo              = Column(String(20))
    veiculo_id        = Column(Integer, ForeignKey("veiculos.id"))
    transportadora_id = Column(Integer, ForeignKey("transportadoras.id"))
    volume_total_m3   = Column(Numeric(6, 2))
    peso_total_kg     = Column(Numeric(8, 2))
    valor_total_nf    = Column(Numeric(12, 2))
    ocupacao_pct      = Column(Numeric(5, 2))
    horario_coleta    = Column(Time)
    status            = Column(String(20), default="planejada")
    justificativa     = Column(Text)
    criado_em         = Column(DateTime, default=datetime.utcnow)

    plano             = relationship("PlanoDia",      back_populates="ondas")
    veiculo           = relationship("Veiculo",       back_populates="ondas")
    transportadora    = relationship("Transportadora", back_populates="ondas")
    programacoes      = relationship("ProgramacaoColeta", back_populates="onda")

    # many-to-many com remessas via tabela associativa
    remessas          = relationship("Remessa", secondary="onda_remessas",
                                    primaryjoin="Onda.id == OndaRemessa.onda_id",
                                    secondaryjoin="OndaRemessa.remessa_id == Remessa.id")


class OndaRemessa(Base):
    __tablename__ = "onda_remessas"
    onda_id    = Column(Integer, ForeignKey("ondas.id"),    primary_key=True)
    remessa_id = Column(Integer, ForeignKey("remessas.id"), primary_key=True)
    sequencia  = Column(Integer)


class ProgramacaoColeta(Base):
    __tablename__ = "programacoes_coleta"

    id                  = Column(Integer, primary_key=True)
    onda_id             = Column(Integer, ForeignKey("ondas.id"))
    transportadora_id   = Column(Integer, ForeignKey("transportadoras.id"))
    canal               = Column(String(20), default="email")
    destinatario_email  = Column(String(200))
    assunto             = Column(String(300))
    corpo               = Column(Text)
    arquivo_anexo       = Column(String(500))
    enviado_em          = Column(DateTime)
    status_envio        = Column(String(20), default="pendente")
    confirmado_em       = Column(DateTime)
    protocolo           = Column(String(100))
    veiculo_confirmado  = Column(String(50))
    criado_em           = Column(DateTime, default=datetime.utcnow)

    onda                = relationship("Onda",           back_populates="programacoes")
    transportadora      = relationship("Transportadora", back_populates="programacoes")


class EventoRastreio(Base):
    __tablename__ = "eventos_rastreio"

    id              = Column(Integer, primary_key=True)
    remessa_id      = Column(Integer, ForeignKey("remessas.id"))
    transportadora  = Column(String(20))
    codigo_rastreio = Column(String(100))
    status          = Column(String(50))
    localizacao     = Column(String(200))
    detalhe         = Column(Text)
    evento_em       = Column(DateTime)
    capturado_em    = Column(DateTime, default=datetime.utcnow)
    fonte           = Column(String(30))

    remessa         = relationship("Remessa", back_populates="eventos_rastreio")


class Alerta(Base):
    __tablename__ = "alertas"

    id              = Column(Integer, primary_key=True)
    tipo            = Column(String(50), nullable=False)
    severidade      = Column(String(20), default="media")
    titulo          = Column(String(200))
    descricao       = Column(Text)
    remessa_id      = Column(Integer, ForeignKey("remessas.id"))
    cliente_id      = Column(Integer, ForeignKey("clientes.id"))
    cd_id           = Column(Integer, ForeignKey("centros_distribuicao.id"))
    resolvido       = Column(Boolean, default=False)
    resolvido_em    = Column(DateTime)
    criado_em       = Column(DateTime, default=datetime.utcnow)

    remessa         = relationship("Remessa", back_populates="alertas")
    cliente         = relationship("Cliente", back_populates="alertas")


class TabelaPrecoTransportadora(Base):
    __tablename__ = "tabela_preco_transportadoras"

    id                = Column(Integer, primary_key=True)
    transportadora_id = Column(Integer, ForeignKey("transportadoras.id"), nullable=False)

    # Identificação granular da rota
    macro_regiao      = Column(String(30), nullable=False)   # 'Sudeste','Sul', etc.
    estado            = Column(String(50), nullable=False)   # 'São Paulo', etc.
    uf                = Column(String(2),  nullable=False)   # 'SP','RJ', etc.
    classificacao     = Column(String(20), nullable=False)   # 'Capital' | 'Interior'

    cobertura         = Column(Boolean, default=True)        # False = não atende rota

    # Fracionado
    preco_por_kg      = Column(Numeric(8, 4))
    peso_minimo_kg    = Column(Numeric(8, 2))
    preco_minimo      = Column(Numeric(10, 2))
    prazo_frac_dias   = Column(Integer)

    # FTL
    preco_ftl_fixo    = Column(Numeric(10, 2))
    prazo_ftl_dias    = Column(Integer)

    # Encargos (aplicam a ambos)
    ad_valorem_pct    = Column(Numeric(6, 4))   # decimal: 0.0015 = 0.15%
    gris_pct          = Column(Numeric(6, 4))
    sla_confirmacao_h = Column(Integer)

    observacoes       = Column(Text)
    validade_inicio   = Column(Date)
    validade_fim      = Column(Date)
    ativo             = Column(Boolean, default=True)
    criado_em         = Column(DateTime, default=datetime.utcnow)
    atualizado_em     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def regiao(self) -> str:
        return f"{self.macro_regiao} — {self.estado} — {self.classificacao}"

    transportadora    = relationship("Transportadora", back_populates="tabela_precos")


class HistoricoEventos(Base):
    __tablename__ = "historico_eventos"

    id                = Column(Integer, primary_key=True)
    timestamp         = Column(DateTime, default=datetime.utcnow, index=True)
    tipo_evento       = Column(String(50), nullable=False, index=True)
    origem            = Column(String(30), nullable=False)
    ator_tipo         = Column(String(20), nullable=False)
    ator_nome         = Column(String(100), nullable=True)
    remessa_id        = Column(Integer, ForeignKey("remessas.id"), nullable=True, index=True)
    transportadora_id = Column(Integer, ForeignKey("transportadoras.id"), nullable=True, index=True)
    cd_id             = Column(Integer, ForeignKey("centros_distribuicao.id"), nullable=True, index=True)
    descricao         = Column(Text, nullable=False)
    resultado         = Column(String(30), nullable=False)
    gravidade         = Column(String(10), nullable=True)
    visibilidade      = Column(String(10), default="interno")
    dados_extra       = Column(JSON, nullable=True)


class OportunidadeConsolidacao(Base):
    __tablename__ = "oportunidades_consolidacao"

    id                  = Column(Integer, primary_key=True)
    cd_id               = Column(Integer, ForeignKey("centros_distribuicao.id"))
    regiao              = Column(String(50))
    data_analise        = Column(Date)
    qtd_clientes        = Column(Integer)
    volume_atual_m3     = Column(Numeric(6, 2))
    tipo_atual          = Column(String(20))
    tipo_possivel       = Column(String(20))
    economia_estimada   = Column(Numeric(10, 2))
    acao_sugerida       = Column(Text)
    status              = Column(String(20), default="aberta")
    criado_em           = Column(DateTime, default=datetime.utcnow)


class TipoErro(Base):
    __tablename__ = "tipos_erro"

    id            = Column(Integer, primary_key=True)
    codigo        = Column(String(50), unique=True, nullable=False)
    descricao     = Column(String(200), nullable=False)
    gravidade     = Column(String(10), nullable=False)   # "info" | "alerta" | "critico"
    acao_sugerida = Column(Text, nullable=True)


class ErroAcao(Base):
    __tablename__ = "erro_acoes"

    id                       = Column(Integer, primary_key=True)
    tipo_erro_codigo         = Column(String(50), ForeignKey("tipos_erro.codigo"), nullable=False)
    acao                     = Column(String(30), nullable=False)
    # "retry_automatico" | "escalar_humano" | "bloquear_remessa" | "ignorar_log"
    max_tentativas           = Column(Integer, default=1)
    intervalo_retry_segundos = Column(Integer, default=30)
    ativo                    = Column(Boolean, default=True)

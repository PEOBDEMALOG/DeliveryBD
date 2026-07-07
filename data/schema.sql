-- ============================================================
-- PEO-BD: Schema do Banco de Dados
-- PostgreSQL 15+ (ou SQLite para MVP local)
-- ============================================================

-- ─── PARÂMETROS OPERACIONAIS ──────────────────────────────

CREATE TABLE centros_distribuicao (
    id              SERIAL PRIMARY KEY,
    codigo          VARCHAR(10) UNIQUE NOT NULL,  -- 'OSA', 'ITJ'
    nome            VARCHAR(100) NOT NULL,
    cidade          VARCHAR(100),
    uf              CHAR(2),
    sistema_origem  VARCHAR(20) NOT NULL,          -- 'SAP', 'UPS_WMS'
    capacidade_dia  INTEGER,                       -- volumes/dia
    ativo           BOOLEAN DEFAULT TRUE,
    criado_em       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE veiculos (
    id              SERIAL PRIMARY KEY,
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    tipo            VARCHAR(30) NOT NULL,          -- 'truck','vuc_eletrico','vuc_combustao','van'
    placa           VARCHAR(10),
    proprietario    VARCHAR(20) NOT NULL,          -- 'frota_propria','dhl','ups'
    capacidade_m3   NUMERIC(6,2) NOT NULL,
    capacidade_kg   NUMERIC(8,2) NOT NULL,
    ativo           BOOLEAN DEFAULT TRUE
);

CREATE TABLE transportadoras (
    id              SERIAL PRIMARY KEY,
    codigo          VARCHAR(20) UNIQUE NOT NULL,   -- 'DHL','UPS','FROTA_BD'
    nome            VARCHAR(100) NOT NULL,
    email_operacoes VARCHAR(200),
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    integracao      VARCHAR(20) DEFAULT 'email',   -- 'email','edi','api'
    sla_resposta_h  INTEGER DEFAULT 2,             -- horas para confirmar coleta
    ativo           BOOLEAN DEFAULT TRUE
);

CREATE TABLE clientes (
    id              SERIAL PRIMARY KEY,
    codigo_sap      VARCHAR(20) UNIQUE,
    codigo_ups      VARCHAR(20),
    razao_social    VARCHAR(200) NOT NULL,
    cnpj            VARCHAR(18),
    tipo            VARCHAR(30) NOT NULL,          -- 'hospital','laboratorio','universidade','pesquisador'
    cidade          VARCHAR(100),
    uf              CHAR(2),
    cep             VARCHAR(9),
    regiao          VARCHAR(30),                   -- 'capital_sp','interior_sp','sul','etc'
    tem_armazenagem BOOLEAN DEFAULT FALSE,
    janela_inicio   TIME,
    janela_fim      TIME,
    janela_flexivel BOOLEAN DEFAULT FALSE,
    perfil_volume   VARCHAR(20) DEFAULT 'fracionado',  -- 'fracionado','ftl','misto'
    volume_medio_m3 NUMERIC(6,2),
    contrato_ata    BOOLEAN DEFAULT FALSE,
    prazo_ata_dias  INTEGER,
    criado_em       TIMESTAMP DEFAULT NOW(),
    atualizado_em   TIMESTAMP DEFAULT NOW()
);

-- ─── REMESSAS ─────────────────────────────────────────────

CREATE TABLE remessas (
    id              SERIAL PRIMARY KEY,
    numero_remessa  VARCHAR(20) UNIQUE NOT NULL,   -- '8000123' (SAP) ou 'U-44201' (UPS)
    origem          VARCHAR(20) NOT NULL,           -- 'SAP','UPS_WMS'
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    cliente_id      INTEGER REFERENCES clientes(id),
    data_extracao   DATE NOT NULL,
    data_upload     TIMESTAMP DEFAULT NOW(),
    upload_id       INTEGER,                        -- FK -> uploads

    -- Carga
    volume_m3       NUMERIC(6,3),
    peso_kg         NUMERIC(8,2),
    valor_nf        NUMERIC(12,2),
    qtd_volumes     INTEGER,

    -- Status e tipo
    status          VARCHAR(30) DEFAULT 'novo',    -- 'novo','planejado','em_rota','entregue','devolvido'
    tipo_entrega    VARCHAR(20),                   -- 'ftl','fracionado','dedicado'
    prioridade      VARCHAR(20) DEFAULT 'normal',  -- 'normal','alta','critica'

    -- ATA / contrato público
    is_ata          BOOLEAN DEFAULT FALSE,
    numero_empenho  VARCHAR(50),
    prazo_empenho   DATE,
    dias_restantes  INTEGER GENERATED ALWAYS AS (
                        CASE WHEN prazo_empenho IS NOT NULL
                        THEN (prazo_empenho - CURRENT_DATE)::INTEGER
                        ELSE NULL END
                    ) STORED,

    -- NF
    nf_emitida      BOOLEAN DEFAULT FALSE,
    numero_nf       VARCHAR(20),
    chave_nfe       VARCHAR(50),

    -- Deduplicação
    hash_remessa    VARCHAR(64),                   -- SHA256 para dedup
    duplicata_de    INTEGER REFERENCES remessas(id),

    -- Janela
    janela_inicio   TIME,
    janela_fim      TIME,
    janela_critica  BOOLEAN DEFAULT FALSE,

    criado_em       TIMESTAMP DEFAULT NOW(),
    atualizado_em   TIMESTAMP DEFAULT NOW()
);

-- ─── UPLOADS ──────────────────────────────────────────────

CREATE TABLE uploads (
    id              SERIAL PRIMARY KEY,
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    usuario         VARCHAR(100),                  -- 'timoteo','carlos'
    arquivo_nome    VARCHAR(300),
    arquivo_path    VARCHAR(500),
    formato         VARCHAR(10),                   -- 'csv','xlsx'
    total_linhas    INTEGER,
    linhas_validas  INTEGER,
    linhas_erro     INTEGER,
    linhas_dup      INTEGER,
    status          VARCHAR(20) DEFAULT 'processando', -- 'processando','ok','erro'
    log_erros       JSONB,
    criado_em       TIMESTAMP DEFAULT NOW()
);

ALTER TABLE remessas ADD CONSTRAINT fk_upload
    FOREIGN KEY (upload_id) REFERENCES uploads(id);

-- ─── PLANEJAMENTO DE ONDAS ────────────────────────────────

CREATE TABLE planos_dia (
    id              SERIAL PRIMARY KEY,
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    data_plano      DATE NOT NULL,
    ciclo           INTEGER DEFAULT 1,             -- 1=manhã, 2=tarde (fechamento de mês)
    status          VARCHAR(20) DEFAULT 'rascunho', -- 'rascunho','aprovado','executando','concluido'
    criado_por      VARCHAR(100),
    aprovado_por    VARCHAR(100),
    aprovado_em     TIMESTAMP,
    total_remessas  INTEGER,
    total_volume_m3 NUMERIC(8,2),
    total_peso_kg   NUMERIC(10,2),
    total_valor_nf  NUMERIC(14,2),
    otif_realizado  NUMERIC(5,2),
    criado_em       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE ondas (
    id              SERIAL PRIMARY KEY,
    plano_id        INTEGER REFERENCES planos_dia(id),
    numero_onda     INTEGER NOT NULL,
    nome            VARCHAR(100),                  -- 'Onda 01 — Capital SP matutino'
    regiao          VARCHAR(50),
    tipo            VARCHAR(20),                   -- 'ftl','fracionado','dedicado'
    veiculo_id      INTEGER REFERENCES veiculos(id),
    transportadora_id INTEGER REFERENCES transportadoras(id),
    volume_total_m3 NUMERIC(6,2),
    peso_total_kg   NUMERIC(8,2),
    valor_total_nf  NUMERIC(12,2),
    ocupacao_pct    NUMERIC(5,2),                  -- % de capacidade usada
    horario_coleta  TIME,
    status          VARCHAR(20) DEFAULT 'planejada',
    criado_em       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE onda_remessas (
    onda_id         INTEGER REFERENCES ondas(id),
    remessa_id      INTEGER REFERENCES remessas(id),
    sequencia       INTEGER,                       -- ordem de entrega
    PRIMARY KEY (onda_id, remessa_id)
);

-- ─── PROGRAMAÇÃO DE TRANSPORTADORAS ──────────────────────

CREATE TABLE programacoes_coleta (
    id                  SERIAL PRIMARY KEY,
    onda_id             INTEGER REFERENCES ondas(id),
    transportadora_id   INTEGER REFERENCES transportadoras(id),
    canal               VARCHAR(20) DEFAULT 'email', -- 'email','edi'
    destinatario_email  VARCHAR(200),
    assunto             VARCHAR(300),
    corpo               TEXT,
    arquivo_anexo       VARCHAR(500),
    enviado_em          TIMESTAMP,
    status_envio        VARCHAR(20) DEFAULT 'pendente',
    confirmado_em       TIMESTAMP,
    protocolo           VARCHAR(100),
    veiculo_confirmado  VARCHAR(50),
    criado_em           TIMESTAMP DEFAULT NOW()
);

-- ─── RASTREIO ─────────────────────────────────────────────

CREATE TABLE eventos_rastreio (
    id              SERIAL PRIMARY KEY,
    remessa_id      INTEGER REFERENCES remessas(id),
    transportadora  VARCHAR(20),
    codigo_rastreio VARCHAR(100),
    status          VARCHAR(50),                   -- 'coletado','em_transito','entregue','tentativa','devolvido'
    localizacao     VARCHAR(200),
    detalhe         TEXT,
    evento_em       TIMESTAMP,
    capturado_em    TIMESTAMP DEFAULT NOW(),
    fonte           VARCHAR(30)                    -- 'portal_dhl','portal_ups','whatsapp','manual'
);

-- ─── ALERTAS E INTELIGÊNCIA ───────────────────────────────

CREATE TABLE alertas (
    id              SERIAL PRIMARY KEY,
    tipo            VARCHAR(50) NOT NULL,          -- 'ata_prazo','ftl_sub','janela_critica','nf_pendente','duplicata'
    severidade      VARCHAR(20) DEFAULT 'media',   -- 'baixa','media','alta','critica'
    titulo          VARCHAR(200),
    descricao       TEXT,
    remessa_id      INTEGER REFERENCES remessas(id),
    cliente_id      INTEGER REFERENCES clientes(id),
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    resolvido       BOOLEAN DEFAULT FALSE,
    resolvido_em    TIMESTAMP,
    criado_em       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE tabela_preco_transportadoras (
    id                  SERIAL PRIMARY KEY,
    transportadora_id   INTEGER NOT NULL REFERENCES transportadoras(id),

    -- Identificação da rota (granular: macro → UF → classificação)
    macro_regiao        VARCHAR(30) NOT NULL,   -- 'Sudeste','Sul','Centro-Oeste','Nordeste','Norte'
    estado              VARCHAR(50) NOT NULL,   -- 'São Paulo', 'Minas Gerais', etc.
    uf                  CHAR(2)     NOT NULL,   -- 'SP','RJ','MG', etc.
    classificacao       VARCHAR(20) NOT NULL,   -- 'Capital' | 'Interior'

    -- Compatibilidade: campo composto lido por queries legadas
    regiao              VARCHAR(100) GENERATED ALWAYS AS
                            (macro_regiao || ' — ' || estado || ' — ' || classificacao) STORED,

    cobertura           BOOLEAN DEFAULT TRUE,   -- FALSE = não atende esta rota

    -- Fracionado
    preco_por_kg        NUMERIC(8, 4),          -- R$/kg
    peso_minimo_kg      NUMERIC(8, 2),          -- peso mínimo por coleta (kg)
    preco_minimo        NUMERIC(10, 2),         -- R$ mínimo por coleta
    prazo_frac_dias     INTEGER,                -- dias úteis fracionado

    -- FTL (carga fechada)
    preco_ftl_fixo      NUMERIC(10, 2),         -- R$/viagem
    prazo_ftl_dias      INTEGER,                -- dias úteis FTL

    -- Encargos (aplicam a ambos os tipos)
    ad_valorem_pct      NUMERIC(6, 4),          -- ex: 0.0015 = 0.15% sobre NF
    gris_pct            NUMERIC(6, 4),          -- gerenciamento de risco % NF
    sla_confirmacao_h   INTEGER,                -- horas para confirmar coleta

    observacoes         TEXT,
    validade_inicio     DATE,
    validade_fim        DATE,
    ativo               BOOLEAN DEFAULT TRUE,
    criado_em           TIMESTAMP DEFAULT NOW(),
    atualizado_em       TIMESTAMP DEFAULT NOW(),

    UNIQUE (transportadora_id, uf, classificacao)
);

CREATE INDEX idx_tabela_preco_uf   ON tabela_preco_transportadoras(uf, classificacao, ativo);
CREATE INDEX idx_tabela_preco_transp ON tabela_preco_transportadoras(transportadora_id, ativo);

CREATE TABLE oportunidades_consolidacao (
    id              SERIAL PRIMARY KEY,
    cd_id           INTEGER REFERENCES centros_distribuicao(id),
    regiao          VARCHAR(50),
    data_analise    DATE,
    qtd_clientes    INTEGER,
    volume_atual_m3 NUMERIC(6,2),
    tipo_atual      VARCHAR(20),                   -- 'fracionado'
    tipo_possivel   VARCHAR(20),                   -- 'ftl'
    economia_estimada NUMERIC(10,2),
    acao_sugerida   TEXT,
    status          VARCHAR(20) DEFAULT 'aberta',  -- 'aberta','em_analise','aprovada','descartada'
    criado_em       TIMESTAMP DEFAULT NOW()
);

-- ─── ÍNDICES ──────────────────────────────────────────────

CREATE INDEX idx_remessas_data     ON remessas(data_extracao);
CREATE INDEX idx_remessas_status   ON remessas(status);
CREATE INDEX idx_remessas_hash     ON remessas(hash_remessa);
CREATE INDEX idx_remessas_ata      ON remessas(is_ata, prazo_empenho);
CREATE INDEX idx_remessas_cd       ON remessas(cd_id);
CREATE INDEX idx_alertas_tipo      ON alertas(tipo, resolvido);
CREATE INDEX idx_rastreio_remessa  ON eventos_rastreio(remessa_id);

-- ─── DADOS INICIAIS ───────────────────────────────────────

INSERT INTO centros_distribuicao (codigo, nome, cidade, uf, sistema_origem, capacidade_dia) VALUES
    ('OSA', 'CD Osasco', 'Osasco', 'SP', 'SAP', 10000),
    ('ITJ', 'CD Itajaí', 'Itajaí', 'SC', 'UPS_WMS', 5000);

INSERT INTO transportadoras (codigo, nome, email_operacoes, cd_id, integracao, sla_resposta_h) VALUES
    ('DHL',      'DHL Express Brasil',   'operacoes.sp@dhl.com',        1, 'email', 2),
    ('UPS',      'UPS Brasil',           'coletas.sc@ups.com',          2, 'email', 2),
    ('FROTA_BD', 'Frota Própria BD SP',  NULL,                          1, 'interno', 0);

INSERT INTO veiculos (cd_id, tipo, proprietario, capacidade_m3, capacidade_kg) VALUES
    (1, 'truck',         'dhl',         30.0,  10000.0),
    (1, 'vuc_eletrico',  'frota_propria', 20.0,  3500.0),
    (1, 'vuc_combustao', 'frota_propria', 10.0,  2000.0),
    (1, 'van',           'frota_propria',  3.0,   600.0),
    (1, 'van',           'frota_propria',  3.0,   600.0),
    (1, 'van',           'frota_propria',  3.0,   600.0),
    (2, 'truck',         'ups',         30.0,  10000.0);

-- 固收问数最小演示。金额均为人民币元，仅用于展示计算链路。
-- 初始化仅在新数据卷上自动执行；CREATE IF NOT EXISTS / INSERT IGNORE
-- 使手动重复运行不会覆盖用户已有数据。
CREATE DATABASE IF NOT EXISTS ontology_demo
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE ontology_demo;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS portfolios (
  portfolio_id VARCHAR(32) PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  business_type ENUM('FIXED_INCOME', 'EQUITY') NOT NULL,
  access_group ENUM('FI_TEAM', 'RESTRICTED') NOT NULL,
  INDEX idx_portfolio_scope (access_group, business_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS securities (
  security_id VARCHAR(32) PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  asset_class ENUM('CREDIT_BOND', 'RATE_BOND', 'EQUITY', 'CASH') NOT NULL,
  INDEX idx_security_class (asset_class)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS holdings (
  holding_id VARCHAR(32) PRIMARY KEY,
  portfolio_id VARCHAR(32) NOT NULL,
  security_id VARCHAR(32) NOT NULL,
  account_id VARCHAR(32) NOT NULL,
  valuation_date DATE NOT NULL,
  market_value_cny DECIMAL(20, 2) NOT NULL,
  CONSTRAINT fk_holding_portfolio FOREIGN KEY (portfolio_id)
    REFERENCES portfolios (portfolio_id),
  CONSTRAINT fk_holding_security FOREIGN KEY (security_id)
    REFERENCES securities (security_id),
  -- 一条记录代表组合、证券、账户在某个估值日的持仓。
  UNIQUE KEY uq_holding_grain
    (portfolio_id, security_id, account_id, valuation_date),
  INDEX idx_holding_date_portfolio (valuation_date, portfolio_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS portfolio_valuations (
  portfolio_id VARCHAR(32) NOT NULL,
  valuation_date DATE NOT NULL,
  net_asset_value_cny DECIMAL(20, 2) NULL,
  holdings_complete BOOLEAN NOT NULL,
  PRIMARY KEY (portfolio_id, valuation_date),
  CONSTRAINT fk_valuation_portfolio FOREIGN KEY (portfolio_id)
    REFERENCES portfolios (portfolio_id),
  CONSTRAINT ck_holdings_complete CHECK (holdings_complete IN (0, 1)),
  INDEX idx_valuation_date (valuation_date)
) ENGINE=InnoDB;

INSERT IGNORE INTO portfolios
  (portfolio_id, name, business_type, access_group) VALUES
  ('P001', '稳健固收一号', 'FIXED_INCOME', 'FI_TEAM'),
  ('P002', '稳健固收二号', 'FIXED_INCOME', 'FI_TEAM'),
  ('P003', '临界值组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P004', '信用精选组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P005', '纯利率债组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P006', '零净资产组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P007', '缺失净资产组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P008', '负净资产组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P009', '持仓未齐组合', 'FIXED_INCOME', 'FI_TEAM'),
  ('P010', '权益业务组合', 'EQUITY', 'FI_TEAM'),
  ('P011', '受限固收组合', 'FIXED_INCOME', 'RESTRICTED'),
  ('P012', '缺失估值快照组合', 'FIXED_INCOME', 'FI_TEAM');

INSERT IGNORE INTO securities (security_id, name, asset_class) VALUES
  ('CB001', '示例信用债甲', 'CREDIT_BOND'),
  ('CB002', '示例信用债乙', 'CREDIT_BOND'),
  ('RB001', '示例国债', 'RATE_BOND'),
  ('EQ001', '示例股票', 'EQUITY'),
  ('CA001', '示例现金资产', 'CASH');

-- 2026-09-14 的正常固收组合：P001 12%、P002 8%、P003 恰好 10%、
-- P004 25%、P005 无信用债且持仓完整，应算作 0%。
-- P006～P009 是不可计算的数据异常；P012 故意没有当日估值快照。
INSERT IGNORE INTO portfolio_valuations
  (portfolio_id, valuation_date, net_asset_value_cny, holdings_complete) VALUES
  ('P001', '2026-09-14', 1000.00, 1),
  ('P002', '2026-09-14', 1000.00, 1),
  ('P003', '2026-09-14', 1000.00, 1),
  ('P004', '2026-09-14', 1000.00, 1),
  ('P005', '2026-09-14', 1000.00, 1),
  ('P006', '2026-09-14',    0.00, 1),
  ('P007', '2026-09-14',    NULL, 1),
  ('P008', '2026-09-14', -100.00, 1),
  ('P009', '2026-09-14', 1000.00, 0),
  ('P010', '2026-09-14', 1000.00, 1),
  ('P011', '2026-09-14', 1000.00, 1),
  -- 更早日期只为 P001/P004 提供完整快照，验证查询日期不会串用。
  ('P001', '2026-09-11', 1000.00, 1),
  ('P004', '2026-09-11', 1000.00, 1);

INSERT IGNORE INTO holdings
  (holding_id, portfolio_id, security_id, account_id, valuation_date, market_value_cny) VALUES
  -- 同一证券跨两个账户持有，必须相加，不能按证券简单去重。
  ('H001', 'P001', 'CB001', 'ACC_A', '2026-09-14',  70.00),
  ('H002', 'P001', 'CB001', 'ACC_B', '2026-09-14',  50.00),
  ('H003', 'P001', 'RB001', 'ACC_A', '2026-09-14', 880.00),
  ('H004', 'P002', 'CB001', 'ACC_A', '2026-09-14',  80.00),
  ('H005', 'P002', 'RB001', 'ACC_A', '2026-09-14', 920.00),
  ('H006', 'P003', 'CB002', 'ACC_A', '2026-09-14', 100.00),
  ('H007', 'P003', 'RB001', 'ACC_A', '2026-09-14', 900.00),
  ('H008', 'P004', 'CB001', 'ACC_A', '2026-09-14', 150.00),
  ('H009', 'P004', 'CB002', 'ACC_A', '2026-09-14', 100.00),
  ('H010', 'P004', 'RB001', 'ACC_A', '2026-09-14', 750.00),
  ('H011', 'P005', 'RB001', 'ACC_A', '2026-09-14', 1000.00),
  ('H012', 'P006', 'CB001', 'ACC_A', '2026-09-14', 200.00),
  ('H013', 'P007', 'CB001', 'ACC_A', '2026-09-14', 200.00),
  ('H014', 'P008', 'CB001', 'ACC_A', '2026-09-14', 200.00),
  ('H015', 'P009', 'CB001', 'ACC_A', '2026-09-14', 500.00),
  ('H016', 'P010', 'CB001', 'ACC_A', '2026-09-14', 500.00),
  ('H017', 'P010', 'EQ001', 'ACC_A', '2026-09-14', 500.00),
  ('H018', 'P011', 'CB001', 'ACC_A', '2026-09-14', 500.00),
  ('H019', 'P011', 'RB001', 'ACC_A', '2026-09-14', 500.00),
  ('H020', 'P012', 'CB001', 'ACC_A', '2026-09-14', 500.00),
  ('H101', 'P001', 'CB001', 'ACC_A', '2026-09-11',  90.00),
  ('H102', 'P001', 'RB001', 'ACC_A', '2026-09-11', 910.00),
  ('H103', 'P004', 'CB002', 'ACC_A', '2026-09-11',  50.00),
  ('H104', 'P004', 'RB001', 'ACC_A', '2026-09-11', 950.00);

-- 官方镜像先给 MYSQL_USER 授予数据库级全部权限，此处收窄为只读。
-- 应用只能查询；表结构和演示数据的初始化由独立 root 账号完成。
-- 镜像授予的 schema 名会转义下划线；按未转义的 schema 撤权可能报 1141。
-- demo 是本容器专用账号，先清除其所有授权，再授予精确 schema 的 SELECT。
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'demo'@'%';
GRANT SELECT ON ontology_demo.* TO 'demo'@'%';

# 固收智能问数最小 Demo

用一个可编辑的业务模型，将“信用债占净资产比例超过 10% 的固收组合有哪些？”转换成明确的查询计划，再生成参数化 SQL，在真实 MySQL 中执行并输出解释。

自然语言理解使用受限句式的模拟解析器，**没有调用大模型，也不需要 API Key**。重点是验证业务模型如何决定计算过程。金额为虚构的人民币元，估值日固定使用示例日期 2026-09-14。

## 运行

需要 Python 3.10+、Docker Desktop 已启动、本机已有 `mysql:8.4` 镜像。首次运行会建立 Python 虚拟环境并安装 PyMySQL。

```bash
git clone https://github.com/max2055/DataIntelligence.git
cd DataIntelligence
./scripts/run_demo.sh
```

脚本启动专用数据库 `ontology_demo`，监听 `127.0.0.1:13316`。它与本机已有的 3306 数据库独立。演示账号为 `demo`，密码为 `demo-local-only`，只授予 SELECT；这些是公开的本地演示配置。

预期结果：

| 组合 | 名称 | 信用债市值 | 净资产 | 占比 |
|---|---|---:|---:|---:|
| P001 | 稳健固收一号 | 120.00 | 1000.00 | 12.00% |
| P004 | 信用精选组合 | 250.00 | 1000.00 | 25.00% |

报告在 `output/latest/report.md`。同目录保存意图 JSON、计划 JSON、SQL、绑定参数、审计 SQL 和实际结果 JSON。SQL 文件中的 `%(p1)s` 等占位符由 PyMySQL 绑定，不能直接当作已填入参数的 SQL 粘贴执行。

查看边界条件“不低于 10%”（会额外返回 P003）：

```bash
.venv/bin/python demo.py --question '2026年9月14日，信用债占净资产比例不低于10%的固收组合有哪些？' --output output/inclusive
```

直接提交结构化意图，或不连接数据库只看编译过程：

```bash
.venv/bin/python demo.py --intent examples/intent.json --output output/from-intent
python3 demo.py --plan-only
```

不带日期的“信用债占比超过10%的固收组合有哪些？”也可解析，报告会明示采用的示例日期和默认分母口径。未知指标、母子组合等不支持的句式会返回错误。

## 模型如何产生查询计划

`model/ontology.json` 是模型定义，涵盖以下内容：

| 模型部分 | 本例的含义 |
|---|---|
| `objects`、`relationships` | 组合、证券、持仓快照、估值快照，以及关联键和关联基数 |
| `sources` | 业务属性到表和字段的映射，以及每行数据的粒度 |
| `scopes` | 固收组合的范围条件 |
| `measures` | 信用债市值按组合与日期汇总；净资产读取同粒度的唯一快照 |
| `metrics` | 分子 / 分母、时间粒度、分类口径、完整性及异常规则 |
| `access_policy` | 执行端注入的用户组筛选 |

`data_intelligence/engine.py` 中的三个入口分别负责：

1. `parse_question()`：模拟语言理解，将中文问题绑定为对象、指标、日期、比较符、阈值等模型 ID 与参数。
2. `build_plan()`：查指标定义，展开分子与分母依赖，解析物理字段、分类关联、粒度和权限，生成六个操作节点。
3. `compile_plan()`：只读取计划，不再读取问题或本体模型，按固定操作规则生成 SQL 与绑定参数。

六个计划节点与 SQL 的对应：

| 节点 | 操作 | SQL 中的含义 |
|---|---|---|
| `scope` | scan | 筛选 FI_TEAM 有权查询的固收组合 |
| `numerator` | aggregate | 持仓关联证券分类，按日期筛选，按组合及日期 SUM 市值 |
| `denominator` | scan | 读取同一估值日的净资产和持仓完整性标志 |
| `calculated` | ratio | 关联已汇总的分子与唯一分母，计算占比并标注数据质量 |
| `evaluated` | compare | 与阈值比较，生成是否命中的标记 |
| `result` | project_matches | 仅输出命中的组合及要求的字段 |

P001 同一信用债在两个账户分别持有 70 元与 50 元，必须先汇总得到 120 元，再关联一次 1000 元净资产。因此不会按证券去重丢失持仓，也不会因 JOIN 放大分母。

修改模型的字段映射、分类筛选或度量字段，就会改变计划与 SQL；在编译器已支持的操作范围内，无需修改 Python。新增任意业务计算并不会自动获得支持：这个编译器仅实现“单日组合、汇总分子 / 快照分母”的操作集合。

## 样例数据和验证

`database/init.sql` 建立 4 张表，写入 12 个组合、5 个证券、24 条持仓和 13 条估值快照，覆盖：

- 12%、8%、恰好 10%、25%、无信用债且数据完整的 0%。
- 净资产为零、负数、NULL、持仓不完整、估值快照缺失；这些组合的占比不可计算。
- 非固收组合、无权限组合，以及另一个估值日。

结果与计算解释在同一个只读一致性快照事务内查询。解释表保留范围内未命中和数据异常的组合，以便核对排除原因。

```bash
# 包含真实 MySQL 的集成测试（数据库需先启动）
RUN_MYSQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

测试也验证修改模型分类为利率债后，实际查询随之变化；测试中的模型修改只在内存中发生。

## 数据库管理与范围

```bash
# 仅启动数据库
./scripts/start_demo_db.sh
# 停止 demo，保留数据
docker compose stop mysql
```

数据保存在专用卷 `data-intelligence-demo-mysql-data`，重启会保留。初始化 SQL 只在新卷上自动执行；修改 SQL 后不会自动覆盖已有数据。`CREATE IF NOT EXISTS` 和 `INSERT IGNORE` 允许补齐缺失对象和样例行，但不会更新已存在的数据。自定义端口可用 `DEMO_MYSQL_PORT=13317 ./scripts/run_demo.sh`；亦可用 `MYSQL_IMAGE` 指定已有的兼容镜像。

本例的 FI_TEAM 身份由程序固定注入，未实现登录。只读数据库账号能读取整个演示库，行范围由应用编译的查询限制；业务模型属于受信配置。这不是生产级身份和行权限系统。

未实现真实 LLM、图数据库、任意自然语言查询、母子组合穿透、历史分类、币种换算或收益率。后续可用大模型替换模拟解析器，让其输出相同的结构化意图；后面的模型校验、计划和 SQL 编译仍由程序完成。

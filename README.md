# 拆表 Chaibiao · 可追溯的历史财报工作台

输入沪深北A股股票代码或公司名称，选择公开数据源实际可用的年度、季度或半年报，查看原始三张财务报表、确定性指标和数据核验结果。也支持离线JSON导入和 `SKILL.md` 调用入口。

本项目曾用于 PAYATHON 2026 参赛并在 SkillPay 上架：[关联商品页](https://skillpay.alipay.com/shelf/product?productId=P0806000210801590&merchantId=2088842500658783)。该链接不代表本地模拟订单具有真实支付能力，商品当前状态及服务内容以平台页面为准。代码使用AI辅助实现。

## 快速开始

需要Python 3.10+，运行只用标准库，无需安装第三方Python包或配置模型密钥。

```bash
git clone https://github.com/shuyili94-byte/chaibiao.git
cd chaibiao
python server.py --open
```

Windows也可双击 `启动拆表.cmd`。浏览器打开 `http://127.0.0.1:8765`；保留终端，按Ctrl+C停止。端口占用时：

```bash
python server.py --port 8766 --open
```

首次真实查询需要网络。公开接口可能限流、缺期或改变结构，失败时会显示错误，不会用演示数据冒充真实结果。

## 可以做什么

- 输入六位股票代码、带交易所的代码或公司名，选择准确公司。
- 按实际返回的报告期选择年报、一季报、半年报或三季报。
- 查看资产负债表、利润表、现金流量表的原始科目和单位。
- 对适用的非金融企业生成15项指标和5项检查，保留公式、输入、来源和资料缺口。
- 支持本地网页、在线JSON命令行和离线JSON计算。

金融企业可查看原始三表，不套用普通非金融企业指标。报告期列表不能保证覆盖公司上市以来每期、每张表或每个披露版本。

## 命令行与Skill入口

在仓库根目录执行：

```bash
python skill_cli.py search "贵州茅台"
python skill_cli.py periods 600519
python skill_cli.py report 600519 2025-06-30 --output report.json
```

先确认公司，再从 `periods` 实际返回的日期中选择报告期。代码和日期只作调用示例。在线输出包含原始报表、来源、获取时间、标准化输入与解读；已有输出文件会拒绝覆盖。

完整调用契约、错误码和适用边界见 [SKILL.md](SKILL.md)。Skill是对这些确定性工具的操作说明，不是已接入大模型的自主任务编排器。

## 离线演示与自行导入

```bash
python cli.py data/demo.json --output demo-report.json
```

`data/demo.json` 使用虚构公司和构造数字。真实输入从 [导入模板](data/import-template.json) 开始，字段定义见 [schema.json](data/schema.json)。金额统一人民币百万元、同一合并范围，缺失项保留 `null`。

离线 `cli.py --output` 会原子替换已有目标，使用新文件名保留旧报告；在线 `skill_cli.py --output` 则拒绝覆盖。

## 财报口径

- 资产负债表是期末余额；利润表和现金流量表是本年年初至报告期末的累计数。
- 半年报不等于第二季度单季，三季报不等于第三季度单季。中期指标不自动年化。
- 数据可能后续更新或重述，不能将历史聚合快照当作首次披露版本或无前视回测输入。
- `insufficient` 表示资料不足，`review` 表示差异待查，不改写成“通过”或推断造假。
- 本工具用于财务数据整理与会计学习，不提供估值、评级、审计结论或交易建议。

## 本地数据与订单演示

服务默认只监听 `127.0.0.1`，使用随机访问令牌、Host/跨站请求检查和请求体限制，定位为本机单用户程序。

界面的本地订单、模拟付款和“¥3/份”用于开发联调，不会真实收款，也不是支付宝沙箱交易；它与平台上Skill商品的买断销售不是同一计费流程。`var/orders.sqlite3` 会保存本地订单、导入数据和交付报告，已从Git排除。

源码不包含用户订单、个人财报、登录凭证或会话令牌。不要把该本机原型直接作为公网收费服务部署；公开部署需要另行实现账户、隔离和正式支付。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"
node --test tests/frontend.test.cjs
```

Node.js只用于开发时测试前端，不是程序运行依赖。默认Python套件包含一个显式关闭的联网测试，结果应区分通过和跳过。需要自行做实时接口检查时，可在有网络环境下显式设置 `CHAIBIAO_LIVE_TEST=1`，不要把历史联网记录当作当前接口保证。

测试数据为小规模公开响应裁剪和虚构故障用例，来源、取得日期、裁剪方法见 [测试夹具说明](tests/fixtures/market/README.md)。线上请求失败时不会读取这些fixture兜底。

## 目录

| 文件 | 用途 |
|---|---|
| `market_data.py` | 公司搜索、报告期列表、公开三表读取和口径标准化 |
| `core.py` | 财务指标、检查、公式和说明 |
| `server.py` | 本地工作台与模拟订单 |
| `cli.py` / `skill_cli.py` | 离线计算 / 在线工具调用 |
| `static/` | 本地网页资源 |
| `data/` | 虚构演示、模板和字段模式 |
| `tests/` | Python、前端和接口快照测试 |

原创代码采用[MIT许可](LICENSE)。公开接口数据与资料的权利仍归各自权利方，详见[第三方说明](THIRD_PARTY_NOTICES.md)。

# 公开市场数据测试快照

取得日期：2026-10-22。均为无需登录即可读取的公开公司资料和财务数据，不含用户财报、Cookie、账号凭证或私有数据。fixture 不用于应用中的真实数据兜底；应用联网失败时应显示错误。

## 新浪三表 JSON

`sina_fzb.json`、`sina_lrb.json`、`sina_llb.json` 分别为贵州茅台资产负债表、利润表和现金流量表响应的报告期子集。保留 2026-06-30、2025-06-30、2024-12-31、2024-06-30、2000-12-31 中实际存在的记录；原科目、金额、币种、合并类型与服务商元数据未改写。仅裁剪 `report_date` 与 `report_list`，并将 `report_count` 同步为保留期数。原始请求约含 103/103/99 期，测试快照不表示完整历史。

公开请求示例：

- [资产负债表](https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022?paperCode=sh600519&source=fzb&type=0&page=1&num=1000)
- [利润表](https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022?paperCode=sh600519&source=lrb&type=0&page=1&num=1000)
- [现金流量表](https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022?paperCode=sh600519&source=llb&type=0&page=1&num=1000)
- [AKShare 官方端点实现](https://github.com/akfamily/akshare/blob/main/akshare/stock_fundamental/stock_finance_sina.py)

独立核对：发行人 [2025 半年度报告摘要](https://www.moutaichina.com/mtgf/articleFileDir/2025-08/18/6265fb3851c54af78133a4f6b020e9b5.pdf) 中营业收入 89,389,354,416.84 元、归母净利润 45,402,962,298.10 元、经营现金流净额 13,119,061,031.33 元、期末资产 292,257,789,095.51 元与上述快照一致。

服务商历史 `publish_date` 可能是更新/重述关联日期，不能直接视为该期首次披露日期。比较期使用独立历史记录，不保证等同所选公告中的重述比较列。

## 东方财富搜索

`search_600519.json`、`search_000001.json`、`search_中国平安.json`、`search_920001.json` 保留公开搜索返回，用于验证代码与名称候选、同代码指数/基金排除、港股/期货排除、北交所京 A（其 Classify 可能仍为 NEEQ）保留。

[公开搜索示例](https://searchapi.eastmoney.com/api/suggest/get?input=600519&type=14&count=10)。请求不携带 token。公司资料字段依据 [AKShare 官方实现](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_info_em.py)，测试内保存初次实际成功响应中的 f57/f58/f127/f189 字段；后续该资料端点出现断连，模块提供其他公开来源或明确错误。

## 新浪公司资料 HTML 投影

`profile_sina.html` 与 `industry_sina.html` 仅保留实际页面中上市日、行业和邻接概念表格的内容，去除脚本、追踪、公司联系方式及登录表单。用它验证纯文本表格解析与“保险重仓”概念不能被当作保险行业。

- [公司简介](https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/600519.phtml)
- [所属行业](https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/stockid/600519/menu_num/2.phtml)

离线测试中另外构造的缺表、错误币种、极端数字、分页不推进和季度日期仅属于故障/契约用例；未写入公开 fixture 冒充真实披露。真实联网测试须显式设置 `CHAIBIAO_LIVE_TEST=1`，不在普通测试中反复请求数据源。

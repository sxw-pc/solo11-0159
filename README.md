# 分类垃圾收运交接核台

离线虚构的分类投放、车辆分舱收运和末端称重交接证据账。批次抽检、装载双见证及终端净重分别裁定；迟到高污染抽检会隔离已装载批次、归零分类绩效，原交接记录仍可追溯。所有证据、文件哈希和决策版本持久化在 SQLite。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m wastehandoff demo --db demo.sqlite
python -m wastehandoff demo-unload --db unload.sqlite
python -m wastehandoff audit --db unload.sqlite
python -m unittest discover -s tests -v
```

独立入口是 `sort-import`、`route-import`、`weigh-import`、`unload-import`，用 `--db` 和 `--file` 传入仓内 `fixtures/*-ok.json` 或 `*-bad.json`。`report` 返回业务表、分类绩效、卸料采录与凭证版本和源证据；`audit` 在全新内存库从原始文件重放并逐表比对。所有车辆、站点和人员是合成身份，不接真实 GPS 或地磅。

## 离线卸料采录与来源净重核定

整车票 `accepted` 只证明封签整车净重；`weigh` 按装车比例写出的 `handoff` 分摊值是记账假设，不是某一来源实际交接质量的证据。`unload-import` 追加保存每次开舱采录：容器码（可漏扫、可重扫）、批次封签锚定、卸前/卸后秤读数、班次共同零点标定区间、独立见证与原始采集摘要（`raw_digest`），以及闭舱确认和独立补扫。

核定规则（`unload-adjudication-v1`）：同一封签的分段读数必须成链；每班只有一个共同零点修正 z，由 `Σ原读数 − n·z = 整车净重` 唯一解出并须落在标定区间内；同码容器归同一来源，漏码段可在封签来源集合内自由归属，各来源合计不得超过装车 kg。所有完整相容解释被枚举后：

- 每个来源批次给出净重上下界及造成边界的原始票号；同一来源在不同解释中净重不同的，只记区间，不任选冒充确数。
- 没有任何完整解释（断链、零点冲突、锚定越界、质量守恆失败等）时整车进入 `pending_investigation`，绩效记 `pending_verification`，绝不用比例值填平。
- 方案甲（整车交接合同）：校准整车净重与封签内合格来源确认总量一致时签发整车凭证，批次栏注明尚未归属；合格来源失格即撤销。
- 方案乙（来源结算合同）：某批次在所有相容解释中净重相同才签发确定公斤数凭证并计入绩效，否则保持待核。
- 两条合同路线对同一批原件同时核定，主营凭证由整车票上的进口合同号（`contract_no` → `contract.form`）决定。

迟到撤销抽检、取消装载、独立复秤和秤台标定冲突都会触发重裁：核定、区间、凭证与绩效全部追加新版本，原始读数、原整车票与旧 handoff 一律保留不覆盖。`demo-unload` 依次演示漏码、补扫、晚到撤样、复秤四种变化。

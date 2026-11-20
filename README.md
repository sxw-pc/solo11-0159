# 分类垃圾收运交接核台

离线虚构的分类投放、车辆分舱收运和末端称重交接证据账。批次抽检、装载双见证及终端净重分别裁定；迟到高污染抽检会隔离已装载批次、归零分类绩效，原交接记录仍可追溯。所有证据、文件哈希和决策版本持久化在 SQLite。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m wastehandoff demo --db demo.sqlite
python -m wastehandoff audit --db demo.sqlite
python -m unittest discover -s tests -v
```

独立入口是 `sort-import`、`route-import`、`weigh-import`，用 `--db` 和 `--file` 传入仓内 `fixtures/*-ok.json` 或 `*-bad.json`。`report` 返回三套业务表、分类绩效和源证据；`audit` 在全新内存库从原始文件重放并逐表比对。所有车辆、站点和人员是合成身份，不接真实 GPS 或地磅。

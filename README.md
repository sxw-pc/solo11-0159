# 分类垃圾收运交接核台

离线虚构的分类投放、车辆分舱收运和末端称重交接证据账。批次抽检、装载双见证及终端净重分别裁定；迟到高污染抽检会隔离已装载批次、归零分类绩效，原交接记录仍可追溯。所有证据、文件哈希和决策版本持久化在 SQLite。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m wastehandoff demo --db demo.sqlite
python -m wastehandoff audit --db demo.sqlite
python -m unittest discover -s tests -v
```

独立入口是 `sort-import`、`route-import`、`weigh-import`，用 `--db` 和 `--file` 传入仓内 `fixtures/*-ok.json` 或 `*-bad.json`。`report` 返回三套业务表、分类绩效和源证据；`audit` 在全新内存库从原始文件重放并逐表比对。所有车辆、站点和人员是合成身份，不接真实 GPS 或地磅。

## 分隔板对照试验（baffle）

`baffle-import` 录入试点点位换板对照试验，`baffle-sheet`（可选 `--trial`）输出可执行采录单和证据归因。正式抽检仍由 `sort-import` 独立裁定合格/污染；试验只回答少掉的杂物量是否真由换板产生。在任何严格对照成立前，8→5 的差值只给松散区间 `[0,3]`——换板贡献 3 或全由入料贡献 0 两个真实来料世界同时相容。

两种获准对照，均以合成整数档登记，不接真实设备：

- `split_source`：同一车保留标准料分入两个一次性暂存夹具，同轮在旧/新隔板下使用，必须登记双向等份见证（`baffle_retain` → 两份 `baffle_issue`）。夹具拆封只读一次，未知拆封（`baffle_unknown_unseal`，每试验一轮）后不能重新分组拿回旧入料。
- `crossover`：改装点相邻班次交替隔板（`baffle_switch`，需 `empty_verified` 空料原件），未换板点以同源入料（`baffle_origin_link`）同时留对照。只有跨班入料变化被同时对照证实、且切换无残留时才核证；效果为观测差值减去受控入料偏移。

每份读数在采样前登记控制来历（同源料、班次、点位设备历史、可否彼此对照）。来自不同入料数的最大改善与最小杂物不许合并成一个隔板结论；单个读数合格不保证对照成立。采录单的 `plan` 给出现场在有限试次（两点两班、三份读数、一轮未知拆封）内可兑现的首测与下一步；无动作可兑现且仍存在令结论相反的真实来料世界时判 `permanently_inconclusive`。签出的区间（`baffle_attest`）始终以同一料源和同一设备历史成立，并随 `audit` 从原件重放一致。

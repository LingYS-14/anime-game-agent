# KISEKI 夜间实机开发报告（2026-11-08 03:00–07:00）

> 作者：DSH 编码代理（受 LingYS-14 授权自主执行）
> 仓库：https://github.com/LingYS-14/anime-game-agent （private）
> 运行环境：Windows、2560x1600、DPI 120%、崩坏：星穹铁道 4.6.0、终末地 1.6.3、DSH + kiseki MCP

## 1. 一句话摘要

把 `kiseki_runtime` 从「能跑单测的骨架」推进到「在本机真实游戏上可用的 M0」：
星铁完成启动→自动登录→HUD/手机/地图锚点实机校准→冷启动全流程可复现；
终末地确认只能做「只读副驾」（ACE 过滤合成输入，未尝试绕过）；
dsh-superagent 的 issue→ticket→worker→PR 链路在 Windows 上跑通，已产出待审 PR #2。

## 2. 完成事项与证据

### 2.1 星铁（崩坏：星穹铁道 4.6.0）

| 事项 | 结果 | 证据 |
| --- | --- | --- |
| 启动链路 | 直接启动 `StarRail.exe` → 标题画面（约 40 s）→ 点「点击进入」→ 黑屏约 30 s → 自动登录进游戏 | `captures/*cold2-ingame*.png` |
| 冷启动闭环 | 杀进程 → 重启 → `game_skill('hsr','enter_game')` 77.1 s → 已在派对车厢 | devlog §9、`captures/20261108-034610-hsr-cold2-ingame-527f3b.png` |
| HUD 锚点 | 右上 7 图标/小地图/任务/灯泡/笔记本/聊天/冲刺，共 14 个 | `configs/hsr.yaml` |
| 手机 UI | Esc 开/关；四列网格 22 个图标逐一校准；点 `phone_mail` 成功打开短信界面 | `captures/20261108-034902-...png` |
| 地图 UI | `m` 键打开导航/星轨航图；5 个地图锚点；`map_close` 点击关闭成功 | `captures/20261108-034922-hsr-map-open-c2769a.png` |
| 自动化约束 | 所有输入前校验前台窗口；dry_run 默认；F12 急停；付费/抽卡/删除动作拒绝 | 代码 + 20 个单测 |

当前 `configs/hsr.yaml` 共 **42 个锚点**，加载零错误；`Runtime` 支持热加载（改 yaml 不需重启进程）。

### 2.2 终末地（明日方舟：终末地 1.6.3）

- 只能经鹰角启动器进游戏（直接启动 exe 会弹「请通过官方启动器打开游戏」）。
- **ACE 反作弊会过滤 SendInput 合成输入**：ESC、点击、W、F 在游戏内全部无效（截图逐帧对比无变化），
  同一套输入在星铁完全有效。
- 结论：终末地定位「只读副驾」——窗口定位、截图、识别与提示，操作由人执行；**不尝试绕过 ACE**（合规红线）。
- `configs/endfield-map.yaml` 已写入启动路径、ACE 结论与 3 个只读锚点。

### 2.3 dsh-superagent 多 agent 协作

- 把 superagent 的 `source:github` 切到本仓库（revision 3）。
- 遇到并修复：旧仓库线程残留导致新仓库 poll 404 → 清理 `orchestrator.sqlite` 中 `github:*` 线程/游标
  （备份 `orchestrator.sqlite.bak-kiseki-20261108`），写回 `github-since`。
- 以 LingYS-14 身份在 issue #1 评论 `@LingYS-14-agent` 触发通知 → 60 s 内建 ticket → worker 独立 worktree
  完成 → 开 **PR #2**「ci: add GitHub Actions workflow for unit tests on windows-latest」（20 行，MERGEABLE）。
- 工单进入 `stage=person/action=review`，按「人审合并」原则**未自动合并**，留给用户。

### 2.4 基础设施修复

- **全屏 D3D 截图旧帧**：窗口 DC BitBlt 抓不到实时画面 → 改为「前台用屏幕 DC 按客户区裁剪，
  后台用 PrintWindow，再回退窗口 DC」，返回 `backend` 字段。
- **launcher 代理防护**：`scripts/dsh-superagent.ps1` 启动前探测代理端点，死代理则清空 6 个代理变量直连。
- **DSH `.dsh/.env` 死代理**（127.0.0.1:7897，Clash 未运行）：已注释并备份，避免所有 git/gh/游戏子进程走死代理。
- **游戏启动必须清代理**：星铁继承 `HTTP_PROXY` 会报「网络连接错误」；已在 profile notes 与报告里写明。

## 3. 仓库状态

- 分支 `main`，提交（本地）：
  - `db59907` M0: kiseki_runtime MCP skeleton
  - `9d2bcbc` M0.1: profile hot-reload, click hover settle, hsr HUD/phone anchors
  - `ca998eb` M0.2: endfield profile + ACE read-only
  - `12239cb` M0.3: enter_game waits out splash + superagent/cold-start devlog
  - `a5897b2` M0.3: calibrated phone grid and map anchors
- 远端：已推送 `ca998eb`；`12239cb`/`a5897b2` 因 GitHub 连接间歇性阻断，由后台重试脚本推送中。
- 单测：20 个全部通过；`--self-check` 正常；仓库私有，`vendor/GameScaling/` 与 `captures/` 已 ignore。

## 4. 遇到的问题与教训（按重要度）

1. **切仓库必须清状态**：superagent 以「仓库 + 编号」为键，切仓后旧线程编号在新仓库全部 404，
   会持续拖垮 poll 健康。→ 已写进 devlog 与本次报告。
2. **MCP 进程缓存 profile**：改 yaml 后锚点不生效 → 已加 mtime 指纹热加载。
3. **启动 splash 期间点击无效**：`wait stable` 不能判断标题已加载 → `enter_game` 改为固定等 42 s。
4. **Unity 需要 hover 帧**：click 前需移动后停 50 ms，否则 UI 不响应。
5. **代理环境变量污染子进程**：DSH 的 `.env` 是全局性的，任何 git/gh/游戏启动都可能受影响 → launcher 已加防护，
   `.env` 死代理行已注释。

## 5. 待你决定（明早讨论）

1. **合并 PR #2**：CI 工作流本身要在合并后才会在 main 上跑；合并后在 PR 上补一条绿链即可。
2. **仓库公开性**：继续 private，还是转 public（代码已无隐私内容，但含游戏自动化说明）。
3. **superagent 是否常驻维护本仓库**：现在是切到本仓库、人审合并模式；可继续用它接「文档/CI/测试」类工单。
4. **终末地路线**：「只读副驾」是否足够，还是要换更安全的注入方式（如截图识别 + 人的操作提示）。
5. **M1 方向**：优先做「视觉感知（模板匹配/OCR）」还是「技能编排（任务 DSL）」——建议先做模板匹配，
   让锚点从手工坐标升级为可校验的视觉定位。

## 6. 下一步建议（M0.1 → M1）

- M0.1（1–2 天）：把锚点从「固定坐标」升级为「模板 + 区域 + 阈值」；给 `game_skill` 加前置/后置截图校验；
  补 `tests/` 中锚点 profile 的契约测试；把 42 个锚点的来源截图归档到 `docs/anchors/`。
- M1（1 周）：星铁只读场景先跑通——主界面识别、任务列表读取、体力/邮箱提示；
  不做自动战斗/抽卡；所有输入仍要求前台 + dry_run 默认。
- 长期：终末地只读副驾 + 星铁 UI 自动化，参考 GameScaling 但不引入其重依赖。

## 7. 合规与风险

- 未读取游戏内存、未注入 DLL、未绕过 ACE/反作弊；只做窗口截图与合法 SendInput。
- 只使用小号（UID 107760946 / 165165603），未进行付费、抽卡、删除类操作。
- 游戏自动化可能违反 ToS，风险由使用者承担；本仓库默认 `dry_run: true`。

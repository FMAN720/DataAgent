# 分析交付扩展：设计系统核验

日期：2026-10-25。范围：现有分析对话的图表、导出、历史与已确认条件恢复。依据 `design/analysis-delivery.md` 的 Operate / local extension 约定核验；没有创建新视觉世界。

结论：**No changes to the incumbent design system**。当前扩展与 `DESIGN.md` 的颜色、字体、控件、平面层级、响应式布局和文字状态约定一致。保留 `DESIGN.md` 与 `PRODUCT.md` 原文；没有新建或重写 sidecar。本文件只记录本次检查，不构成新令牌或全局规则。

## 检查依据与边界

- 完整读取 `~/.codex/skills/impeccable/agents/impeccable_documenter.toml` 与同 skill 的 `reference/document.md`，按普通扩展保留现有系统的角色要求执行。
- 系统文件：`DESIGN.md`、`PRODUCT.md`；方向文件：`design/analysis-delivery.md`。检查 `.impeccable/design.json`，当前不存在。
- 实物源码：完整读取 `app/web/workspace.css`，读取 `app/web/index.html`，抽样核对 `workspace.js` 的分析表、结果排序、条件、历史、导出和三种 SVG 图表实现。
- 视觉证据：查看 `captures/desktop.png`、`mobile.png`、`desktop-contribution.png`、`desktop-trend.png`。截图包含合成演示声明，不作为真实模型正确率或企业数据证明。
- 本次仅静态源码与已交付截图核验，没有安装、下载、调用模型、打开浏览器或启动服务；没有执行新的运行时键盘交互。源码证据与截图证据分别记录，不把静态检查写成运行时验证。

## 与现有系统的一致性

| 检查项 | 实物证据 | 结论 |
| --- | --- | --- |
| 色彩 | `DESIGN.md` frontmatter 的 25 个颜色值全部能在当前 CSS 找到；图表使用现有 accent、muted、danger、line，未引入新配色 | 继承 |
| 字体与层级 | 系统中文无衬线栈；页面标题 28px / 手机 25px；区块 17px；分析正文 14px；说明 12px；表头与图例 11px；数字使用 tabular-nums | 继承 |
| 深度与形状 | 无 box-shadow；背景明度和 1px 边线区分层级；控件 8px 圆角、42px 最小高度；原生 select/details 和内联几何 SVG | 继承 |
| 布局 | 保留左导航和 900px 阅读宽度；≤900px 单列；≤600px 图表选择器、导出与历史行顺排；宽图和宽表留在自身滚动容器中 | 局部适配 |
| 状态与操作 | 保存、恢复、历史快照、核验和截断均有文字；历史列表按需展开；条件存在时显示；导出复用现有链接和原生选择框 | 继承 |
| 方向合同 | 标题及费用说明后呈现历史入口；答案顺序为结论、核验、图表、详细数据、导出及证据；来源和精确数据保留 | 匹配 |

本轮新增的 ≤600px 排列适配和 SVG 绘图几何属于该表面的实现，没有作为系统级断点或令牌写回 `DESIGN.md`。

## 已封版修复的核对

1. `workspace.js:279–284` 将日期明确为不含结束日，并分别显示“地区 / 渠道筛选”和“口径规则”。未追加地区或渠道限制不再表达为不存在默认口径；桌面与手机截图显示该区分。
2. `workspace.js:354–356` 的贡献图例明确标示“增加（正值）”“减少（负值）”，与 accent / danger 的正负柱颜色一致；贡献桌面和手机截图可见文字图例。
3. `workspace.js:31–35` 的 `analysisTable` 为整体对比、分组拆解、比例拆解及结果表复用横滚说明、`tabindex="0"`、具名 `role="region"`；默认展开比较表已纳入。`workspace.js:348–350` 为图表提供同类可聚焦区域和窄屏说明；CSS 使用已有 focus 颜色，未创建新焦点风格。

## 五行系统摘要

Palette：纸白工作面、白色字段、浅绿导航、深青操作；图表沿用 accent / muted / danger。
Type：系统中文无衬线；标题 28/25px、区块 17px、分析正文 14px、说明 12px、图例/表头 11px。
Layout：导航加工作区，阅读宽度 900px；手机单列，图表与数据表在自身容器横滚。
Components：原生按钮、选择框、details、SVG；8px 控件圆角、42px 最小高度、平面细线分隔。
Named rules：沿用现有“文字表达状态”“精确数据可核对”“内部横滚”“可见键盘焦点”约束；未新增命名规则或禁令。

## 未 canonize 或修复的既有漂移

`PRODUCT.md` 的“尚无独立前端应用”“自动 Embedding 保持关闭”为已知旧描述，本次没有将其复制为新事实或重写配置；`.impeccable/design.json` 缺失为既有记录缺口；`DESIGN.md` 的额外“分析结果展示（第六阶段）”小节保留现状。三者均不因本次局部扩展而被修复、规范化或升格为新的系统规则。本次抽样未发现需要记录为新全局规则的 craft-floor 缺陷。

## 源文件指纹

检查时 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| DESIGN.md | `3398519EC9C085CD7046CD94432FA026E8A415E552BE5DB204C6FD355B087990` |
| PRODUCT.md | `DD1DB626CB1031D40DEAB3352BDDE98AC36EE642C224C840031ECDB59FA96150` |
| app/web/workspace.css | `AD34F204E46BA4561B58CA81B74CBF9C7C185C9960FF26D91236B58558C56CDF` |
| app/web/workspace.js | `EFEB79EF1DB7D000EE6985C3CA7E0766C3A159303BE0EAD69128B17E534C8059` |
| app/web/index.html | `B0E4855CFC9FCD3BECE272B57E9A0391BE49155823A613EB58EBA11824F2DF30` |

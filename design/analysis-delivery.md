# 分析对话的结果交付扩展

Mode: Operate。范围是现有分析对话页面的局部扩展，继承 DESIGN.md，不替换视觉系统。

## Direction contract

THESIS: 一次分析应当能核对数据、带走结果，并在刷新后继续。图表依附证据，历史与已确认条件属于当前会话的操作上下文。

OWN-WORLD: 沿用纸白底、浅绿色导航、深绿色操作、系统中文字体、细分隔线和现有表格。使用浏览器原生按钮/选择框和几何 SVG，不引入图表框架或远程资源。

STORY: 用户打开历史会话，核对继承的来源、指标、周期和筛选；阅读答案和图表，通过表格/证据复核，按需导出，继续追问时重新查询数据。

FIRST VIEWPORT: 页标题及已有费用说明下方放历史入口与保存状态。历史列表按需展开，确认条件只在存在时显示。答案内按结论、核验状态、图表、详细数据与证据排序。手机保留单列布局，图表和宽表在自身容器内滚动。

FORM: 本地扩展，无新视觉世界；concept seed: not applicable (local extension)。依据 new-work.md §3，局部扩展不运行 concept-seed。加载、空历史、读取失败、未确认保存、结果截断和核验失败都有明确文字；禁用状态和键盘焦点继承现有控件。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

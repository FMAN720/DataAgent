# 图片与扫描件的核对流程

Mode: Operate。既有上传工作台的局部扩展；继承 DESIGN.md 和现有表格、原生控件、纸白/深绿配色，不更换视觉世界。

THESIS: 图片中的数据应先变成可核对的转录，再进入查询和检索；识别本身不构成事实核验。

OWN-WORLD: 复用当前系统字体、细分隔线、状态文字、8px 控件和原生 details；原文件的真实页图承担核对证据，不生成装饰图片。

STORY: 从数据源的图片/扫描件入口上传本地草稿，选择页码，主动确认外发识别，核对原图和编辑文字/CSV表格，保存核对稿后明确转为表格预览或本页参考资料。模型待配置时支持人工转录。

FIRST VIEWPORT: 上传入口、模型未启用/已配置状态及草稿选择在面板顶部。选中草稿后先显示文件、状态、页数和部分处理范围；真实页面和编辑区在宽屏并列、手机单列。费用授权靠近识别操作，人工核对靠近发布操作，互不代替。

FORM: code-led local extension；concept-seed not applicable，依 new-work.md §3 局部扩展豁免。不设新视觉概念或批准 comp。展示本地上传、识别中、部分失败、未保存修改、修订冲突、未启用模型和空结果状态。外发和向量索引默认为不勾选；所有数据作为文字渲染。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

# B4交付记录：URL抓取、来源与网络安全边界

日期：2026-09-09。已验收范围：离线代码/本地HTTP服务器/CLI行为；真实公网服务、
真实搜索服务商、浏览器人工点检与研究写作业务未验收。

## 交付

1. **URL 策略与 SSRF 防护（S2-09 离线）**：`src/harness/ingest/url_policy.py`。
   仅 http/https、禁 userinfo、IDNA/端口校验；私网/回环/链路本地/组播/保留/文档网段
   默认拦截（IPv4-mapped IPv6 按 IPv4 判定）；域名解析结果**整体白名单**——混入任何
   不允许地址即拒绝（防 DNS rebinding 先手）；可信场景（测试服务器/受信 intranet）
   经 `UrlPolicy(allowed_hosts=…)` 显式放行，不提供逐任务私网大闸。
2. **抓取器（S2-02）**：`src/harness/ingest/fetcher.py`。http.client 直连白名单解析出的
   IP；HTTPS 用 `server_hostname` 建立 TLS（保留证书校验）；连接/读取超时、
   下载 10MB 上限（超限即断）、正文类 Content-Type 白名单；重定向每一跳重新
   parse+resolve（限 5 跳）；结果分类 ok/http_error/timeout/network_error/
   too_large/unsupported_type/redirect_limit。
3. **正文提取**：`src/harness/ingest/html_extract.py`（标准库 HTMLParser）——
   标题、段落、h1~h6 转 Markdown，剔除 script/style；text/html、xhtml、text/plain、
   text/markdown 与无类型嗅探；charset 声明/GB18030 兼容。**搜索摘要不会冒充已读正文**
   （搜索未接入；抓取的正文必须完整落库才登记为来源）。
4. **URL 来源登记（S2-05/06 URL侧）**：SourceRecord 增加 final_url/http_status/
   content_type；`SourceStore.add_url` 把抓取层状态映射为存储层分类
   （http_error/timeout/network_error/redirect_limit→read_failed；
   unsupported_type→unsupported；too_large/empty/ok/partial 原样）；
   网页与本地/粘贴同文转载去重复用既有 sha256+空白折叠判重（duplicate_of，不重复存全文）。
5. **统一入口**：TaskRequest.urls/allow_network；新增 `src/application/imports.py`
   `import_request_sources` 统一导入 texts/files/urls（零可用来源整体失败、部分失败
   保留索引、累计总量逐条核算）；ResearchApplication 默认安全策略、可注入
   url_policy；CLI `--import-url`/`--allow-network`；Web 表单增加网页链接输入，
   ⑳面板直接查看网页来源状态与全文。
6. **搜索占位（S2-03/04）**：`src/harness/ingest/search.py`。SEARCH_PROVIDER 未配置
   → 搜索明确禁用（可操作提示，不假搜、不用 Mock 顶替）；未实现服务商显式报"尚未接入"；
   SearchRecord 预留 root_job_id/查询/排名/URL/费用字段，**未知成本显式 None 不记零**
   （S4-10 记账接口的字段已就位）。Settings/.env.example 增加 SEARCH_*（Key repr 隐藏）。
7. **S2-10 结构保证**：网页文本没有执行路径；测试固化——恶意指令原样入来源、
   不改变请求快照/系统指令、工具注册表不变、任务目录外无新文件。

## 使用

```powershell
# 指定网页链接（默认安全策略；公网页面直接可用）
.venv\Scripts\python -m src.interfaces.cli "整理这个网页" --import-url https://example.com/article
# 私网/回环地址会被明确拒绝（退出码1，message 含"不允许的地址"），不会静默抓取
# Web：表单“网页链接”每行一个；运行后⑳面板可见 http状态/最终URL/标题/全文

# 搜索：未配置时任何搜索调用点都得到“搜索服务未配置”提示（当前无调用点）
# 配置后（选定服务商、核验官方接口与费用）在 search.py 接入 provider
```

导入限制沿用产品拟定值：单任务≤20来源；单来源提取正文≤2MB；累计正文≤10MB；
原始下载单页≤10MB（fetcher 上限），超限明确拒绝不静默截断。

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`，330 passed，32.64秒（B3基线255）。
- 定向新增：URL策略49（协议/格式、全套私网与保留网段、mapped形式、白名单放行、
  混合解析拒绝、DNS失败）、抓取与提取9（本地HTTP服务器：正文/跳转复检/重定向环/
  404/图片类型/超限/默认策略拦截回环）、URL来源登记6、URL整链+S2-10共7
  （应用先抓取后运行、失败分类、CLI、网页指令惰性）、搜索网关4。
- 修复：请求校验误伤空粘贴文本（保留 B3 的 empty 分类语义，仅文件/URL 要求非空）。
- 20个业务案例和10个故障案例定义仍有效，业务执行0次；无付费模型/搜索请求、
  无新增第三方依赖、未修改DSH。

## 兼容性、限制与回退

- 新增目录 `src/harness/ingest/`、`src/application/imports.py`；SourceRecord/索引只增字段，
  旧索引可读；TaskRequest 新字段带默认值；request.json 不落网页正文与粘贴正文。
- 无真实搜索服务商：需用户选定并按官方接口/费用核验后接入（S2-03/04 未验收项）。
- 真实公网抓取质量（JS渲染页面、复杂表格）、DNS rebinding 真实样本、代理场景待 S6；
  Web/CLI 暂不暴露私网白名单开关（默认安全，需要时经 ResearchApplication(url_policy=…)）。
- HTML 提取不渲染 JS；抓取不做自动重试（失败明确登记）；PDF（S2-11）独立未做。
- 回退：删除 ingest 与 imports 目录并还原 research/request/cli/workbench 小改即可；
  不要删除用户运行产物（jobs/sources）。

下一步B5：S3 证据→素材→提纲→初稿→审校，把登记的资料接进研究写作链。

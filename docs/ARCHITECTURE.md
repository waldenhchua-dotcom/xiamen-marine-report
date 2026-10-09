# 架构与状态

HTML/CSS/JavaScript 使用同源 HTTP 接口。Python 标准库提供本机服务；取数、校验和绘图使用同一套 CLI 核心，界面没有另一个安全判断分支。

`fetch` 建立唯一 run，原始字节和请求元数据进入 evidence，所有数字通过 source_id 追溯。`manifest.json` 一旦写入不再由后续流程修改。`validate` 校验各原件和规范化值的对应关系，生成可再生 validation.json。渲染只读取冻结数据，候选目录包含 PDF、每地 PNG、来源索引和 manifest 副本；每次新生成一个候选摘要。

默认流程：NEW → FETCHED → VALIDATED → CANDIDATE → AWAITING_REVIEW。任何关键缺口进入 BLOCKED（跨日缺潮表另有 BLOCKED_CROSS_DAY_DATA）。独立审核可形成 APPROVED_REPORT 或 REJECTED。报告文件保留 UNAPPROVED_REFERENCE 字样；分离审核记录不冒充现场批准。

固定时间区为 Asia/Shanghai。第三段的高潮后取严格晚于其时刻的首个整点。需覆盖的日期集合由每个站分别计算，不能把三站终点强制对齐。海洋变量分别请求和登记格点，波高、涌浪、流速、流向、水温不能共用一个未经证明的格点元数据。

网络请求固定白名单；最多两次、有限间隔。失败对象保留状态证据，不自动切海外代理；刷新创建新 run。官方网页获取与正文审核分开，栏目列表不能替代有日期、有覆盖范围的正文。

Optional integrations: 0.1 does not call n8n or Claude and records analysis_provider=none. Existing CLI boundaries are the extension points: scheduler invokes fetch/validate first; only VALIDATED frozen run may be supplied to a future analysis adapter, whose raw result and actual usage must be stored in a new analysis version. Review remains independent and binds the final candidate. Optional external adapters are deferred rather than simulated.

对源码发布使用锁定依赖、离线 CI 和敏感内容扫描。真实取数仅由用户手动执行，CI 默认不调用外网数据源。


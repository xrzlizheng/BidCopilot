workspace "BidCopilot 当前态系统模型" "2026-10-30。证据来自 backend/app、frontend、deploy 与 ADR-0001；运行时验收未在本轮复测。" {

    model {
        platformAdmin = person "平台管理员（企业版）" "开通租户、查看平台审计；仅存在于商业授权部署。"
        tenantAdmin = person "企业管理员" "管理本租户用户、角色、组织、模型密钥与租户设置。"
        bidTeam = person "投标团队" "投标负责人、标书工程师、合规/法务；执行商机到合同的交付主线。"

        bidcopilot = softwareSystem "BidCopilot 投标决策与交付平台" "自托管、多租户；单一镜像多部署形态（ADR-0001）。" {
            nginxProxy = container "nginx 反向代理" "同源入口：/api 与 /healthz 转 FastAPI，/ 转 Next.js；镜像内进程。" "nginx"
            webApp = container "Web 工作台" "Next.js 14 App Router；浏览器端 fetch + TanStack Query；access token 仅存内存。" "Next.js 14.2.21 / Node 22"
            apiService = container "API 服务" "FastAPI 模块化单体：社区版 30 个 router（企业版 +platform）、JWT + RBAC、进程内 APScheduler（8 job）。" "Python 3.12 / FastAPI / SQLAlchemy async" {
                group "投标交付内核" {
                    bidProjects = component "投标项目工作区" "项目、招标文档版本、约束集、评分项、逐条响应、决策卡、就绪清单、证据包、复盘。"
                    tenderParsing = component "招标文档解析" "docx/pdf/txt/md 解析、OCR 调度、资格/评分/废标/预算抽取、风险与分包。"
                    mineruOcr = component "MinerU 本地 OCR" "镜像内置 MinerU 4 与 ONNX 模型；进程内线程执行；与 MiniMax 二选一。"
                    knowledgeBase = component "知识库与检索" "知识库/文档/切片/图片/模板/知识包；ACL 与加权召回。"
                    bidGeneration = component "标书生成" "大纲/章节生成、证据链接、事实核查、审批、docx/暗标导出。"
                    complianceCheck = component "合规预检" "确定性规则集、租户策略、逐条响应/偏离、报告、破玻璃与个人资源。"
                    aiEvaluation = component "AI 评标" "按评分项对章节打分并产出 findings 报告。"
                    pricingStrategy = component "报价策略与报价单" "成本模型、历史价格带 P25/P50/P75、敏感性、三档建议价、报价单不可变快照与 DOCX 导出。"
                    contractLifecycle = component "合同审查与履约" "条款风险、三方一致性、合同台账、审批、履约与变更留痕。"
                    enterpriseAssets = component "企业资产" "企业/人员/资质/业绩/信用/投标记录；字段策略与组织可见性。"
                }
                group "商机与外部数据" {
                    opportunityHub = component "商机中心" "KeepRisk 同步、订阅调度、自动匹配、买家情报、关注。"
                    companyIntel = component "企业标讯画像" "企业维度标讯同步与画像。"
                    dataGateway = component "外部数据网关" "KeepRisk 适配器、快照、搜索客户端与 DataResult 契约。"
                    webSearch = component "联网检索" "web_search 路由与 DeepSeek/MiniMax 提供方适配；计费与审计。"
                }
                group "平台与治理" {
                    identityAuthz = component "身份与授权" "JWT、角色模板、能力判定、组织域角色与资源 ACL。"
                    platformTenant = component "平台租户开通（企业版）" "平台管理员创建租户与企业管理员；社区版不含。"
                    runtimeSettings = component "运行时设置" "模型配置与密钥加密、URL 白名单、AI 状态、决策设置、报价成本参数（pricing-settings）。"
                    costLedger = component "付费调用台账" "配额预占/释放、cost_ledger 与数据运营汇总。"
                    auditLog = component "审计" "追加式事件流；新事件 v2 HMAC 哈希链（历史 v1 SHA-256）；断链时 repair-audit-chain 受控修复并留痕。"
                    notificationsCenter = component "站内消息 + 外推渠道" "订阅新增、截止/履约提醒与审批的站内通知；按租户渠道广播企业微信 Webhook / SMTP 邮件。"
                }
            }
            db = container "PostgreSQL 16" "业务库：76 个迁移（head 0077，社区版链）；租户隔离经 TenantRepository；advisory lock 用于付费调用与调度互斥；审计链 v2 HMAC（断链 repair-audit-chain 修复）。" "PostgreSQL 16" {
                tags "Database"
            }
            redis = container "Redis 7" "登录失败限流计数、/readyz 探活、refresh 重放防护；未承担缓存/队列。" "Redis 7"
            storage = container "对象存储" "MinIO（默认）或本地目录：网关快照、知识库文件/图片、生成中间物。" "MinIO / 本地文件系统"
        }

        keeprisk = softwareSystem "KeepRisk 标讯网关" "商机列表/详情/正文、企业标讯、采购人历史中标；返回 DataResult 状态。" {
            tags "External"
        }
        keepriskMcp = softwareSystem "KeepRisk 企业数据 MCP" "工商/信用/股权关联/尽调/批量核验/企业对比；token 仅环境变量。" {
            tags "External"
        }
        llmGateway = softwareSystem "LLM / Judge 网关" "OpenAI 兼容 /chat/completions；生成、事实核查、AI 评标、合同深审、报价叙述。" {
            tags "External"
        }
        minimaxOcr = softwareSystem "MiniMax VLM OCR" "云端视觉 OCR，/v1/coding_plan/vlm。" {
            tags "External"
        }
        searchApi = softwareSystem "联网搜索 API" "DeepSeek 或 MiniMax 搜索提供方。" {
            tags "External"
        }

        platformAdmin -> nginxProxy "开通租户、查看平台审计" "HTTPS"
        tenantAdmin -> nginxProxy "管理租户、角色与模型密钥" "HTTPS"
        bidTeam -> nginxProxy "商机到合同的交付主线" "HTTPS"

        platformAdmin -> bidcopilot "开通租户、查看平台审计" "HTTPS"
        tenantAdmin -> bidcopilot "管理租户、角色与模型密钥" "HTTPS"
        bidTeam -> bidcopilot "商机到合同的交付主线" "HTTPS"
        bidcopilot -> keeprisk "商机、企业标讯、买家情报" "HTTPS / AppCode"
        bidcopilot -> keepriskMcp "采购人/投标主体/合同相对方情报" "MCP over HTTPS"
        bidcopilot -> llmGateway "生成、评标、深审、报价叙述" "HTTPS"
        bidcopilot -> minimaxOcr "扫描件 OCR" "HTTPS"
        bidcopilot -> searchApi "联网检索" "HTTPS"

        nginxProxy -> webApp "页面与静态资源" "HTTP 127.0.0.1:3000"
        nginxProxy -> apiService "/api、/healthz" "HTTP 127.0.0.1:8000"
        webApp -> apiService "无 nginx 形态下的 /api rewrite" "HTTP BACKEND_URL"

        apiService -> db "业务读写（TenantRepository）" "SQLAlchemy async"
        apiService -> redis "登录限流、就绪探活、refresh 重放防护" "Redis"
        apiService -> storage "快照、知识文件、图片" "S3 API / 本地 FS"
        apiService -> keeprisk "商机同步、企业标讯、买家情报" "HTTPS / AppCode，含调度器"
        apiService -> keepriskMcp "企业情报（工商/信用/股权/尽调）" "MCP over HTTPS"
        apiService -> llmGateway "生成、评标、深审、报价叙述" "HTTPS"
        apiService -> minimaxOcr "扫描件 OCR" "HTTPS"
        apiService -> searchApi "联网检索" "HTTPS"

        bidProjects -> tenderParsing "解析招标、澄清与补遗" "in-process"
        bidProjects -> enterpriseAssets "读取资质、业绩与字段策略" "in-process"
        bidProjects -> notificationsCenter "投标节点提醒" "in-process"
        tenderParsing -> mineruOcr "本地 OCR（未配 MiniMax 或指定 mineru）" "in-process"
        tenderParsing -> minimaxOcr "云端 OCR（auto 且已配置）" "HTTPS"
        knowledgeBase -> tenderParsing "文档入库解析" "in-process"
        knowledgeBase -> storage "保存知识文件与图片" "S3 API"
        bidGeneration -> knowledgeBase "检索证据、模板与知识包" "in-process"
        bidGeneration -> bidProjects "读取项目、评分项与响应" "in-process"
        bidGeneration -> storage "保存生成快照与图片" "S3 API"
        bidGeneration -> llmGateway "章节生成与事实核查" "HTTPS"
        complianceCheck -> bidProjects "读取招标文档与逐条响应" "in-process"
        complianceCheck -> knowledgeBase "读取切片与图片证据" "in-process"
        complianceCheck -> bidGeneration "复用 docx 构建与章节" "in-process"
        complianceCheck -> enterpriseAssets "读取企业与字段策略" "in-process"
        aiEvaluation -> bidGeneration "复用 LLM 客户端与章节" "in-process"
        aiEvaluation -> bidProjects "读取评分项" "in-process"
        aiEvaluation -> llmGateway "逐评分项打分" "HTTPS"
        pricingStrategy -> bidProjects "读取项目、约束与结果" "in-process"
        pricingStrategy -> llmGateway "策略叙述（未配置时降级模板）" "HTTPS"
        contractLifecycle -> tenderParsing "解析合同与招标文本" "in-process"
        contractLifecycle -> bidProjects "对照投标承诺" "in-process"
        contractLifecycle -> notificationsCenter "履约与到期提醒" "in-process"
        opportunityHub -> dataGateway "商机同步与买家情报" "in-process"
        opportunityHub -> enterpriseAssets "资质自检与既有供应商比对" "in-process"
        opportunityHub -> bidProjects "高匹配自动立项" "in-process"
        opportunityHub -> notificationsCenter "订阅新增通知" "in-process"
        opportunityHub -> costLedger "付费配额与调用台账" "in-process"
        companyIntel -> dataGateway "企业标讯同步" "in-process"
        companyIntel -> enterpriseAssets "企业画像与投标记录" "in-process"
        companyIntel -> costLedger "付费配额与调用台账" "in-process"
        dataGateway -> keeprisk "列表、详情与历史中标" "HTTPS"
        dataGateway -> searchApi "搜索请求" "HTTPS"
        dataGateway -> storage "原始响应快照" "S3 API"
        webSearch -> dataGateway "使用搜索客户端" "in-process"
        webSearch -> costLedger "搜索计费" "in-process"
        identityAuthz -> auditLog "登录与用户变更审计" "in-process"
        platformTenant -> identityAuthz "创建租户与企业管理员" "in-process"
    }

    views {
        systemContext bidcopilot "SystemContext" {
            include *
            autolayout lr
        }

        container bidcopilot "Containers" {
            include *
            autolayout tb
        }

        component apiService "BidDeliveryCore" {
            include bidProjects
            include tenderParsing
            include mineruOcr
            include knowledgeBase
            include bidGeneration
            include complianceCheck
            include aiEvaluation
            include pricingStrategy
            include contractLifecycle
            include enterpriseAssets
            include llmGateway
            include minimaxOcr
            include storage
            autolayout tb
        }

        component apiService "OpportunityFlow" {
            include opportunityHub
            include companyIntel
            include dataGateway
            include webSearch
            include enterpriseAssets
            include bidProjects
            include notificationsCenter
            include costLedger
            include keeprisk
            include searchApi
            include storage
            autolayout tb
        }

        styles {
            element "Person" {
                shape Person
                background "#08427B"
                color "#ffffff"
            }
            element "Software System" {
                background "#1168BD"
                color "#ffffff"
            }
            element "External" {
                background "#8C8C8C"
                color "#ffffff"
            }
            element "Container" {
                background "#438DD5"
                color "#ffffff"
            }
            element "Database" {
                shape Cylinder
                background "#2E6DA4"
                color "#ffffff"
            }
            element "Component" {
                background "#85BBF0"
                color "#000000"
            }
        }
    }
}

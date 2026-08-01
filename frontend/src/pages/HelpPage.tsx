import { useEffect } from "react"
import { BookOpen, ExternalLink, ShieldAlert } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import { PageHeader } from "@/components/common"

const sections = [
  {
    id: "quick-start",
    title: "快速开始",
    body: "推荐先判断市场环境，再缩小股票范围，最后核对结构和报告证据。",
    bullets: ["检查市场雷达的数据日期、覆盖率和次日执行仓位", "用热门或智能筛选生成候选池", "运行缠论扫描并进入个股页核对笔、线段、中枢和买卖点", "把研究结论写入自选股备注，并用历史报告进行复盘"],
  },
  {
    id: "market-radar",
    title: "市场雷达",
    body: "评分范围为 0—100。页面同时展示即时环境、平滑确认后的次日仓位、支持证据、风险反证和算法版本。仓位曲线不是简单映射，而是经过 MA5、确认天数和滞回处理。",
    bullets: ["数据日期决定分析基准，不等同于页面打开时间", "可执行仓位以区间表达，默认下一交易日生效", "覆盖不足或字段缺失会在数据口径提示中列出"],
  },
  {
    id: "stock-analysis",
    title: "个股分析",
    body: "个股分析顶部内置自选研究队列，可用上一只、下一只或股票卡片连续切换；顶部全局搜索仍可研究自选股以外的股票。页面读取最近 500 根前复权日线，并自动取得最近一次缠论结构；没有缓存时会提交后台计算任务。",
    bullets: ["可按研究标签过滤自选队列，上一只和下一只只在当前标签范围内切换", "鼠标经过自选股票会预取 K 线与缠论结构，切换后无需离开当前页面", "当前股票的自选标签和研究备注会随行情一起显示", "K 线下方依次展示成交量和 MACD", "笔、线段、中枢、买卖点和背离可以独立开关", "背离沿用旧系统口径：相邻顶/底笔价格创新高或新低、但该笔 MACD 力度减弱时标记；它是辅助确认，不等同于完整多级别缠论背驰判定", "金色表示笔，蓝色表示线段，金框区域表示中枢，红买绿卖，红色底背离、绿色顶背离", "历史研究记录保存算法版本与输入快照，可用于复现"],
  },
  {
    id: "watchlist",
    title: "自选股",
    body: "自选股是个人研究清单，不代表交易指令。可以按代码、名称、标签和备注检索；双击列表中的标签或研究备注即可进入对应编辑状态。",
    bullets: ["研究备注用于记录为什么加入自选、当前判断依据、等待确认条件、失效条件或风险位，以及下一次复查动作；它不参与系统评分", "标签可用中文逗号、英文逗号或空格分隔，并可在个股分析的自选研究队列中快速筛选", "重复加入不会清空已有备注和标签", "移除只删除自选关系，不删除股票、K 线和历史报告"],
  },
  {
    id: "chan-scanner",
    title: "缠论扫描",
    body: "买点和卖点扫描在 Worker 中运行。股票范围留空时扫描全市场，填写代码时只处理指定股票。",
    bullets: ["买点支持一类、盘整背驰、二类、三类 A 和三类 B", "卖点额外支持类二类结构", "最近扫描任务可以回看，结果可按代码、名称或信号过滤、查看规则快照并导出 CSV"],
  },
  {
    id: "market-screening",
    title: "市场筛选",
    body: "热门筛选和智能筛选都会执行缠论扫描，但生成候选池的方式不同。",
    bullets: ["热门：先按涨幅、成交额、成交量、换手率或龙虎榜取前 200 只，再扫描买点", "智能：先按行业、地区和 ST 条件缩小全市场股票池，再执行买点或卖点扫描", "热门适合观察资金焦点，智能适合验证明确研究假设"],
  },
  {
    id: "limit-up-analysis",
    title: "涨停分析",
    body: "双月日历选择完整研究周期。跨日期批量分析会按交易日拆分任务，量化评分、AI 报告和输入快照分别保存。",
    bullets: ["列表保留 7/30/90/180 日涨停次数、题材、原因、连板、换手率和封单", "已有报告会显示日线、周线主升浪评分和综合分", "综合评分由日线、周线、价值、市场、择时和风险等维度组成", "报告详情区分研究员报告与决策报告"],
  },
  {
    id: "ipo-analysis",
    title: "新股分析",
    body: "使用与涨停页相同的日期范围组件，支持近 7 日、近 30 日、近 90 日和本月快捷跨度。",
    bullets: ["列表保留发行价、发行 PE、10 倍和 100 倍潜力评分", "候选日期以接口返回的上市日期为准", "AI 失败不会覆盖已经完成的历史报告", "输入快照保留当次基本面数据"],
  },
  {
    id: "reports",
    title: "研究报告与复现",
    body: "每次分析运行都有独立 ID、类型、日期、状态和算法版本。报告正文、量化指标和原始输入分开存储。",
    bullets: ["报告按 Markdown 安全渲染，表格可横向滚动，原始 HTML 不会执行", "查看报告不会重新调用 AI", "输入快照仅用于复现和审计，不作为实时行情", "算法或提示词发生变化时应发布新版本"],
  },
  {
    id: "jobs",
    title: "任务与状态",
    body: "刷新、扫描和 AI 分析统一返回任务 ID。任务中心显示排队中、执行中、重试中、已完成、部分完成、失败或已取消。",
    bullets: ["部分完成表示主体结果已生成、仍有少量标的或报告失败；展开任务可查看具体失败原因", "补跑失败任务只会重试失败标的或失败子项，不会重跑已经成功的部分", "排队中、执行中或重试中的任务可以主动取消", "相同条件的活动任务默认复用，避免重复点击", "任务时间统一显示为北京时间，并区分提交时间与完成时间", "Worker 重启会恢复未完成任务", "长任务不会阻塞健康检查和普通查询"],
  },
  {
    id: "settings",
    title: "密钥与连接测试",
    body: "密钥使用加密数据库或环境变量保存，页面永不回显明文。保存后可主动测试 Tushare、DeepSeek 或硅基流动连接。",
    bullets: ["连接测试只返回状态和耗时，不返回密钥", "服务器部署时优先使用 HTTPS 和管理员模式", "测试失败时先核对额度、网络和服务端点"],
  },
  {
    id: "automation",
    title: "自动任务",
    body: "当前支持工作日行情更新、热门股票扫描、智能条件筛选和市场雷达刷新。时间统一按 Asia/Shanghai 配置，由 Worker 每 30 秒检查到期计划。",
    bullets: ["修改星期或时间后必须点击保存", "停用后不会再计算下次执行时间", "立即执行会进入任务中心，并遵循去重和重试规则"],
  },
  {
    id: "data",
    title: "数据、缓存与版本",
    body: "默认数据库为 SQLite，启用外键、WAL 和明确事务边界；服务器可切换 PostgreSQL。业务运行只使用 v2 数据库。",
    bullets: ["K 线按股票、周期、复权方式和时间唯一", "历史报告和扫描结果保存在 v2 数据库", "外部数据可能延迟，使用前请检查数据日期"],
  },
  {
    id: "deployment",
    title: "服务器部署",
    body: "服务器使用 AUTH_MODE=admin，由 Nginx 提供 HTTPS，并分别守护 API 与 Worker 进程。",
    bullets: ["限制 CORS 允许来源", "Cookie 使用 HttpOnly、Secure 和 SameSite", "备份数据库、密钥加密主密钥和部署配置"],
  },
  {
    id: "troubleshooting",
    title: "常见故障",
    body: "右上角数据状态取所有模块中最差的一项；点击状态可查看具体是行情、涨停、雷达还是其他模块过期。K 线为空通常表示迁移或行情更新未完成；AI 报告失败优先运行连接测试。",
    bullets: ["浏览器更新后显示旧界面：使用 Ctrl + F5", "任务长期停留执行中：检查 Worker 日志和心跳", "扫描没有结果：放宽股票范围或信号类型后重试"],
  },
] as const

export function HelpPage() {
  const [params] = useSearchParams()
  useEffect(() => {
    const id = params.get("section")
    if (id) document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [params])
  return (
    <div className="page-stack">
      <PageHeader title="帮助中心" description="理解页面逻辑、参数、数据边界与推荐研究流程。" />
      <div className="help-layout">
        <aside className="panel help-toc"><strong>目录</strong>{sections.map((section) => <a key={section.id} href={`#${section.id}`}>{section.title}</a>)}</aside>
        <article className="panel help-content">
          <div className="help-intro"><BookOpen /><div><strong>ChanAnalyzer 研究手册</strong><p>内容与 v2 接口、任务、数据结构和算法版本同步维护。</p></div></div>
          {sections.map((section) => (
            <section id={section.id} key={section.id}>
              <h2>{section.title}</h2>
              <p>{section.body}</p>
              <ul>{section.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul>
            </section>
          ))}
          <div className="risk-note"><ShieldAlert /><div><strong>风险提示</strong><p>所有评分、信号和 AI 报告仅供个人研究，不构成投资建议。市场数据可能延迟或缺失，请自行核验。</p></div></div>
          <a className="doc-link" href="/docs" target="_blank" rel="noreferrer">查看 OpenAPI 接口文档 <ExternalLink size={14} /></a>
        </article>
      </div>
    </div>
  )
}